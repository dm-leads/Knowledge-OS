"""Аудит недели, вторая половина (модуль Д3, Т35.4–Т35.6): пересечения живых страниц, выпавшие из поиска, советы.

- **Пересечения — одним определением с гейтом темы (Р18):** статья проверяется против корпуса без неё самой теми же
  слоями, что `gate topic` (`dedup.run_layers`, без выдачи — слои 1, 2, 5, 6, 7). Статья против продающей — «срочно»,
  статья против статьи — пара, три и больше статей на головной термин — предложение хаба. Сверяются изменившиеся за
  неделю статьи и их соседи по графу ссылок; `--full` — все. Пары пишутся отдельным реестром `data/overlaps.json`, а
  не полем карты спроса: карту перестраивает `map build`, и пометки стёрлись бы.
- **Задачи на склейку** — только если доля ложных тревог гейта замерена и ниже порога (`audit.false_alarm`); иначе
  отчёт так и пишет. Склейку и перенацеливание решает человек: `audit accept <id> --who`.
- **Выпавшие из поиска:** адрес был в индексе в прошлом снимке и пропал — проверка адреса Search Console (не больше
  `audit.inspect_limit` за прогон, счётчик до запроса) и выборка «в поиске» Вебмастера; план — по деньгам страницы.
- **Советы** — задание роли `cheap` файлом (ADR-0006): что дописать слабой статье, с кем связать сироту, почему выпала
  страница; ответ `advice.json` проверяет `audit advice`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core import audit_store, store
from ..core.errors import EngineError, GateRefused, SourceError, source_failures
from ..core.urls import norm_url, section_of
from . import dedup

LIVE_LAYERS = {1, 2, 5, 6, 7}
KINDS = {"sales": ("overlap_sales", "critical", "статья против продающей — срочно"),
         "article": ("overlap_article", "high", "статья против статьи"),
         "hub": ("overlap_hub", "medium", "три и больше статей на головной термин — предложить хаб")}
# Пока доля ложных тревог гейта не замерена ниже порога (audit.false_alarm), пересечения не выше «среднего»: на живом
# корпусе серийные статьи (один шаблон заголовка) дают сотни пар по слоям 1–2 — это шум, а не срочность.
UNMEASURED_LEVEL = {"sales": "medium", "article": "low", "hub": "low"}


def _hashes_path(instance) -> Path:
    return audit_store.folder(instance) / "pages.json"


def changed_pages(instance, crawl, full: bool) -> set[str]:
    """Статьи, чей текст изменился с прошлого аудита (сумма тела), плюс соседи по графу ссылок."""
    path = _hashes_path(instance)
    old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    now = {u: hashlib.sha256((p.get("text") or "").encode("utf-8")).hexdigest()
           for u, p in crawl.pages.items() if p.get("status") == 200}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(now, ensure_ascii=False), encoding="utf-8", newline="\n")
    if full or not old:
        return set(now)
    return {u for u, h in now.items() if old.get(u) != h}


def _neighbours(graph: dict, urls: set[str], limit: int) -> set[str]:
    out = set(urls)
    for u in urls:
        near = list(graph.get(u, [])) + [s for s, t in graph.items() if u in t]
        out.update(near[:limit])
    return out


def live_overlaps(instance, pages, *, ctx=None) -> list[dict]:
    """Пары «статья — другая страница» теми же слоями, что гейт темы (одно определение пересечения, Р18)."""
    from .gate_topic import Candidate
    ctx = ctx or dedup.Context(instance, offline=True)
    sales = {norm_url(u) for u in instance.p["site"]["sales_urls"]}
    out = []
    for pg in pages:
        heads = [h for h in pg.headings if h][:2]
        formulations = list(dict.fromkeys([pg.title] + heads + [pg.title.lower()]))[:3]
        while len(formulations) < 3:
            formulations.append(f"{pg.title} {len(formulations)}")
        cand = Candidate(pg.title, formulations, skip_slugs=frozenset({pg.slug}))
        for r in dedup.run_layers(ctx, cand, LIVE_LAYERS):
            if r.outcome not in (dedup.BLOCK, dedup.WARN) or not r.page:
                continue
            other = norm_url(r.page)
            if other == norm_url(pg.url):
                continue
            kind = "hub" if r.layer == 6 else ("sales" if other in sales or section_of(other) in
                                               instance.p["site"]["sales_sections"] else "article")
            out.append({"pair": sorted([norm_url(pg.url), other]), "kind": kind, "layer": r.layer,
                        "outcome": r.outcome, "reason": r.reason})
    seen, unique = set(), []
    for o in out:
        key = (tuple(o["pair"]), o["kind"])
        if key not in seen:
            seen.add(key)
            unique.append(o)
    return unique


def _save_overlaps(instance, found: list[dict], scope: set[str] | None = None) -> str:
    old = store.read_json(instance.file("overlaps"), "overlaps", missing_ok=True)["records"]
    # пары вне области прогона (без --full) не проверялись — остаются, а не стираются
    kept = [o for o in old if scope is not None and not (set(o["pair"]) & scope)]
    decided = {(tuple(o["pair"]), o["kind"]): o for o in old if o.get("decision")}
    records = []
    for o in found:
        prev = decided.get((tuple(o["pair"]), o["kind"]))
        records.append({**o, "date": instance.today.isoformat(),
                        "decision": prev["decision"] if prev else None, "decided_by": prev.get("decided_by") if prev
                        else None})
    have = {(tuple(r["pair"]), r["kind"]) for r in records}
    records += [o for k, o in decided.items() if k not in have]
    have = {(tuple(r["pair"]), r["kind"]) for r in records}
    records += [o for o in kept if (tuple(o["pair"]), o["kind"]) not in have]
    return store.write_json(instance.file("overlaps"), "overlaps", records)


def merge_tasks_allowed(instance) -> tuple[bool, str]:
    fa = instance.p["audit"]["false_alarm"]
    limit = instance.p["audit"]["false_alarm_max"]
    if fa["rate"] == -1:
        return False, "задачи на склейку не выдаются: доля ложных тревог гейта не замерена (audit.false_alarm)"
    if fa["rate"] >= limit:
        return False, (f"задачи на склейку не выдаются: доля ложных тревог {fa['rate']:.0%} ({fa['as_of']}, "
                       f"{fa['source']}) не ниже порога {limit:.0%}")
    return True, f"задачи на склейку выдаются: доля ложных тревог {fa['rate']:.0%} ({fa['as_of']}, {fa['source']})"


def _index_state(instance, urls: list[str]) -> dict[str, dict]:
    """В индексе ли адреса: Search Console (с пределом проверок) и выборка Вебмастера. Чтение."""
    limit = instance.p["audit"]["inspect_limit"]
    state: dict[str, dict] = {u: {} for u in urls}
    src = instance.p["sources"]
    if src["gsc"]["enabled"]:
        try:
            from ..adapters.gsc_inspect import UrlInspection
            google = UrlInspection.from_profile(instance)
            for u in urls[:limit]:                   # предел считается до запроса
                state[u]["google"] = google.inspect("https://" + u)["indexed"]
        except source_failures() as exc:
            for u in urls:
                state[u].setdefault("google_error", str(exc))
    if src["webmaster"]["enabled"] and instance.p["indexing"]["webmaster"]["enabled"]:
        try:
            from ..adapters.webmaster_write import PAGE, WebmasterWrite
            yandex = WebmasterWrite(instance)
            found, offset = set(), 0
            for _ in range(50):
                answer = yandex.client.call("GET", "search-urls/in-search/samples",
                                            params={"offset": offset, "limit": PAGE})
                chunk = (answer.data or {}).get("samples") or []
                found |= {norm_url(s.get("url", "")) for s in chunk}
                if len(chunk) < PAGE:
                    break
                offset += PAGE
            for u in urls:
                state[u]["yandex"] = u in found
        except source_failures() as exc:
            for u in urls:
                state[u].setdefault("yandex_error", str(exc))
    return state


def dropped(instance, urls: list[str]) -> list[dict]:
    path = audit_store.folder(instance) / "index.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    # Search Console проверяет по давности: сначала адреса, которые не проверялись дольше всех (предел за прогон)
    urls = sorted(urls, key=lambda u: (old.get(u) or {}).get("checked", ""))
    now = _index_state(instance, urls)
    merged = dict(old)
    for u, s in now.items():                 # строка с ошибкой не затирает прежнее «в индексе»
        keep = {k: v for k, v in (old.get(u) or {}).items()}
        keep.update({k: v for k, v in s.items() if not k.endswith("_error")})
        if "google" in s:
            keep["checked"] = instance.today.isoformat()
        merged[u] = keep
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8", newline="\n")
    out = []
    for u, s in now.items():
        for engine in ("google", "yandex"):
            if (old.get(u) or {}).get(engine) is True and s.get(engine) is False:
                out.append({"url": u, "engine": engine})
    return out


def _value_of(instance, url: str) -> float | None:
    """Деньги страницы из карты спроса; неизвестно (нет страницы, нет денег) — None («нет данных»), не ноль."""
    try:
        pages = store.read_json(instance.file("demand_map"), "demand_map", missing_ok=True)["records"]
    except EngineError:
        return None
    for pg in pages:
        if norm_url(pg["url"]) == url:
            value = ((pg.get("value") or {}).get("money") or {}).get("value")
            return None if value is None else float(value)
    return None


def _money_text(value) -> str:
    return "нет данных" if value is None else f"{value:g}"


ADVICE_SCHEMA = """advice:
  - id: "<id находки из аудита>"
    what: "что сделать: дописать раздел …, связать со статьёй …, причина выпадения …"
    link_to: "<адрес из списка кандидатов, только для сирот; иначе пусто>"
tokens: 0
model_used: ""
"""


def advice_task(instance, findings: list[dict], graph: dict) -> Path | None:
    from ..core.tasks import write_task
    weak = [f for f in findings if f["check"] in ("expertise", "orphan", "dropped")]
    if not weak:
        return None
    pages, _ = instance.corpus() if instance.p["corpus"]["folders"] else ([], None)
    lines = ["Находки аудита недели, по которым нужен совет. Отвечай только по ним; адрес для связи сироты — только из",
             "списка кандидатов этой находки.", ""]
    for f in weak[:40]:
        lines.append(f"- {f['id']}: {f['detail']}")
        if f["check"] == "orphan":
            near = sorted({norm_url(p.url) for p in pages if norm_url(p.url) != f["url"]
                           and section_of(p.url) == section_of(f["url"])})[:5]
            lines.append(f"  кандидаты связи: {', '.join(near) or 'нет'}")
            f.setdefault("candidates", near)
    folder = instance.reports_dir()
    return write_task(instance, "audit", "cheap", "советы по находкам аудита", "\n".join(lines),
                      result="advice.json", schema=ADVICE_SCHEMA, folder=folder)


def check_advice(instance, data: dict) -> list[str]:
    """Ответ агента по схеме: номер находки есть в последнем аудите, кандидат связи — из списка кода."""
    snap = audit_store.latest(instance)
    if snap is None:
        return ["аудита нет"]
    by_id = {f["id"]: f for f in snap["findings"]}
    problems = []
    for i, a in enumerate(data.get("advice") or []):
        f = by_id.get(str(a.get("id", "")))
        if f is None:
            problems.append(f"advice[{i}]: находки «{a.get('id')}» в аудите нет")
            continue
        if not str(a.get("what", "")).strip():
            problems.append(f"advice[{i}]: пусто «what»")
        link = str(a.get("link_to") or "").strip()
        if link and norm_url(link) not in (f.get("candidates") or []):
            problems.append(f"advice[{i}]: адрес связи {link} не из кандидатов кода")
    return problems


def run_all(instance, out: dict, *, full: bool) -> dict:
    crawl, graph = out["crawl"], out["graph"]
    first_seen = {f["id"]: f["first_seen"] for f in (out["previous"] or {}).get("findings", [])}
    lines, findings = ["", "## Пересечения живых страниц"], []
    changed = changed_pages(instance, crawl, full)
    scope = _neighbours(graph, changed, instance.p["audit"]["neighbours"])
    pages = [pg for pg in (instance.corpus()[0] if instance.p["corpus"]["folders"] else [])
             if norm_url(pg.url) in scope or full]
    found = live_overlaps(instance, pages)
    lines.append(f"сверено статей {len(pages)}; пар {len(found)}; "
                 f"{_save_overlaps(instance, found, None if full else scope)}")
    allowed, why = merge_tasks_allowed(instance)
    lines.append(why)
    for o in found:
        check, level, text = KINDS[o["kind"]]
        if not allowed:
            # доля ложных тревог не замерена ниже порога: пересечение — сигнал, а не тревога; вердикт дня не красит
            level = UNMEASURED_LEVEL[o["kind"]]
        fid = audit_store.finding_id(check, " ↔ ".join(o["pair"]))
        findings.append({"id": fid, "url": o["pair"][0], "check": check, "level": level,
                         "detail": f"{text}: {' ↔ '.join(o['pair'])} (слой {o['layer']}: {o['reason']})"
                                   + ("; задача на склейку — после «утверждаю»" if allowed else
                                      "; уровень снижен — доля ложных тревог не замерена ниже порога"),
                         "first_seen": first_seen.get(fid, instance.today.isoformat())})
    lines += ["", "## Выпавшие из поиска"]
    articles = sorted(u for u in crawl.pages if section_of(u) in instance.p["site"]["article_sections"]
                      and crawl.pages[u].get("status") == 200 and u in scope)
    gone = dropped(instance, articles)
    for g in sorted(gone, key=lambda g: -(_value_of(instance, g["url"]) or 0)):
        fid = audit_store.finding_id("dropped", f"{g['url']}|{g['engine']}")
        findings.append({"id": fid, "url": g["url"], "check": "dropped", "level": "high",
                         "detail": f"выпала из {g['engine']}; деньги страницы "
                                   f"{_money_text(_value_of(instance, g['url']))}; "
                                   "правка — после «утверждаю»",
                         "first_seen": first_seen.get(fid, instance.today.isoformat())})
    lines.append(f"проверено адресов {len(articles)} (предел проверок Search Console {instance.p['audit']['inspect_limit']}"
                 f"), выпало {len(gone)}")
    # находки прошлого снимка вне области этого прогона не проверялись — переносятся, а не «ушли»
    if not full and out["previous"]:
        from .audit import carry
        mine = {f["id"] for f in findings}
        for f in out["previous"]["findings"]:
            if f["id"] in mine:
                continue
            if f["check"].startswith("overlap"):
                pair = set(f["id"].split("|", 1)[1].split(" ↔ "))     # одно правило с реестром пар
                if not pair & scope:
                    findings += carry([f])
            elif f["check"] == "dropped" and f["url"] not in scope:
                findings += carry([f])
    task = advice_task(instance, out["findings"] + findings, graph)
    if task:
        lines += ["", f"Советы по находкам — задание роли cheap: {task}; ответ проверит `audit advice`"]
    return {"findings": findings, "lines": lines}


def accept(instance, fid: str, who: str, *, reject: bool = False) -> str:
    """«Утверждаю» правку выпавшей страницы или склейку пересечения (Т35.4–Т35.5) — решение человека."""
    if not who.strip():
        raise EngineError("--who — имя человека")
    snap = audit_store.latest(instance)
    if snap is None or not any(f["id"] == fid for f in snap["findings"]):
        raise GateRefused(f"находки «{fid}» в последнем аудите нет")
    f = next(f for f in snap["findings"] if f["id"] == fid)
    if f["check"].startswith("overlap") and not reject and not merge_tasks_allowed(instance)[0]:
        raise GateRefused(merge_tasks_allowed(instance)[1])
    path = audit_store.folder(instance) / "decisions.jsonl"
    decision = "не утверждено" if reject else "утверждено"
    store.append_jsonl(path, [{"date": instance.today.isoformat(), "id": fid, "check": f["check"], "url": f["url"],
                               "decision": decision, "who": who}])
    if f["check"].startswith("overlap"):          # решение по паре — и в реестр пар: карта спроса его видит
        pair = sorted(fid.split("|", 1)[1].split(" ↔ "))
        records = store.read_json(instance.file("overlaps"), "overlaps", missing_ok=True)["records"]
        for r in records:
            if sorted(r["pair"]) == pair:
                r.update(decision=decision, decided_by=who)
        store.write_json(instance.file("overlaps"), "overlaps", records)
    return f"{fid}: {'не утверждено' if reject else 'утверждено'} ({who}) — " + \
        ("в план недели" if not reject else "в план не идёт")


def approved(instance) -> list[dict]:
    path = audit_store.folder(instance) / "decisions.jsonl"
    rows = store.read_jsonl(path, missing_ok=True)
    last = {}
    for r in rows:
        last[r["id"]] = r
    return [r for r in last.values() if r["decision"] == "утверждено"]
