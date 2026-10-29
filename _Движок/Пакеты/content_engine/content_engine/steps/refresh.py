"""Дожим и актуализация (Д4, Т36): `content-engine refresh scan | take`.

- `scan` — кандидаты на правку страницы без сети: (а) зона дожима — запросы последнего снимка позиций пульса на позициях
  `refresh.zone` с показами не ниже `min_impressions`; снимок старше `positions_max_age` дней — «нет данных», а не
  пустой список; показов нет у источника — «нет данных», не «ниже порога»; (б) статьи старше `age_days` — по дню
  выкладки или последней правки в книге ставок, иначе по дате корпуса; даты нет — «нет данных»; (в) утверждённые
  находки поведения (`allow_behavior`); (г) балл ИИ-видимости ниже `ai_visibility.min_score`. Итог — `data/refresh.json`.
- `take <адрес>` — правка встаёт в очередь рядом с новыми темами (Т36.3): тема `refresh-<slug>` типа `refresh`,
  статус `taken`, снимок выдачи по запросам страницы (тот же путь, что `queue take`: квота Keys.so, запасной платный
  источник — через бюджет-гейт) и **неизменяемый эталон** — текст страницы с сайта в день взятия отдельным файлом темы
  с суммой SHA-256. Повторное взятие той же страницы, пока её правка не опубликована, и сверх `weekly_limit` за 7 дней —
  отказ.

Запрет пустого сдвига даты (Т36.4) — проверка `refresh_change` гейта статьи против этого эталона: `change_share`.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from ..core import locks, store
from ..core.errors import EngineError, GateRefused, SourceError
from ..core.number import NO_DATA
from ..core.urls import norm_url, slug_of
from . import queue as queue_mod

TYPE = "refresh"
FORMULATIONS = 5
OPEN = (queue_mod.CANDIDATE, queue_mod.GATED, queue_mod.TAKEN)


def _published_days(instance) -> dict[str, str]:
    """Адрес → день выкладки или последней правки из книги ставок."""
    from ..core import bets
    out = {}
    for r in bets.load(instance):
        days = [r.get("published") or ""] + [x.get("day", "") for x in r.get("refreshed") or []]
        out[norm_url(r["url"])] = max(d for d in days if d) if any(days) else ""
    return out


def _zone(instance, cands: dict, no_data: list) -> dict:
    r = instance.p["refresh"]
    rows = store.read_jsonl(instance.file("positions"), missing_ok=True)
    if not rows:
        return {"status": NO_DATA, "why": "снимков позиций нет — `content-engine pulse run`"}
    last = max(x["date"] for x in rows)
    age = (instance.today - date.fromisoformat(last)).days
    if age > r["positions_max_age"]:
        return {"status": NO_DATA, "why": f"снимок позиций от {last} старше {r['positions_max_age']} дн. — "
                                          "`content-engine pulse run`"}
    lo, hi = r["zone"]
    found = 0
    for x in rows:
        if x["date"] != last or x.get("position") is None or not lo <= x["position"] <= hi:
            continue
        if x.get("impressions") is None:
            no_data.append({"url": x["url"], "kind": "zone", "why": f"«{x['query']}» на {x['position']}: показов "
                                                                     f"у источника {x.get('source', '')} нет"})
            continue
        if x["impressions"] < r["min_impressions"]:
            continue
        found += 1
        _reason(cands, x["url"], {"kind": "zone", "query": x["query"], "engine": x["engine"],
                                  "position": x["position"], "impressions": x["impressions"],
                                  "period": f"{x.get('period_from', '')} — {x.get('period_to', '')}",
                                  "note": f"«{x['query']}»: позиция {x['position']}, показов {x['impressions']}"})
    return {"status": "ок", "snapshot": last, "found": found}


def _age(instance, cands: dict, no_data: list) -> dict:
    days = _published_days(instance)
    limit = instance.p["refresh"]["age_days"]
    found = 0
    for item in instance.index().pages:
        page = item.page
        if page.kind != "article":
            continue
        url = norm_url(page.url)
        day = days.get(url) or page.date
        if not day:
            no_data.append({"url": url, "kind": "age", "why": "дня выкладки нет ни в книге ставок, ни в шапке корпуса"})
            continue
        age = (instance.today - date.fromisoformat(day)).days
        if age >= limit:
            found += 1
            _reason(cands, url, {"kind": "age", "day": day, "age_days": age,
                                 "note": f"последняя выкладка или правка {day} — {age} дн. (порог {limit})"})
    return {"status": "ок", "found": found}


def _ai(instance, cands: dict) -> dict:
    from .ai_visibility import weak
    path = instance.root / "data" / "ai_visibility.jsonl"
    rows = store.read_jsonl(path, missing_ok=True)
    if not rows:
        return {"status": NO_DATA, "why": "балла ИИ-видимости нет — `content-engine ai score`"}
    last = max(x["date"] for x in rows)
    floor = instance.p["ai_visibility"]["min_score"]
    found = 0
    for x in rows:
        if x["date"] == last and weak(x, floor):
            found += 1
            _reason(cands, x["url"], {"kind": "ai", "score": x["score"], "missing": x.get("missing", []),
                                      "note": f"балл ИИ-видимости {x['score']} из {x['max']} измеренных (порог {floor} из 6)"})
    return {"status": "ок", "snapshot": last, "found": found}


def _behavior(instance, cands: dict) -> dict:
    if not instance.p["refresh"]["allow_behavior"]:
        return {"status": "выключено", "why": "refresh.allow_behavior: false"}
    rows = store.read_jsonl(instance.root / "data" / "behavior" / "decisions.jsonl", missing_ok=True)
    found = 0
    for x in rows:
        if x.get("decision") == "утверждено":
            found += 1
            _reason(cands, x["url"], {"kind": "behavior", "finding": x["id"], "note": f"поведение: {x.get('what', '')}"})
    return {"status": "ок", "found": found}


def _reason(cands: dict, url: str, reason: dict):
    url = norm_url(url)
    cands.setdefault(url, {"url": url, "reasons": []})["reasons"].append(reason)


def _open_pages(instance) -> set[str]:
    return {norm_url(t["page"]) for t in queue_mod.load(instance) if t.get("type") == TYPE and t["status"] in OPEN}


def collect(instance) -> tuple[dict, dict, list]:
    """Кандидаты без записи: (адрес → причины, блоки, «нет данных»). Для `scan` и известных ответов."""
    cands: dict[str, dict] = {}
    no_data: list[dict] = []
    blocks = {"zone": _zone(instance, cands, no_data), "age": _age(instance, cands, no_data),
              "ai": _ai(instance, cands), "behavior": _behavior(instance, cands)}
    return cands, blocks, no_data


def scan(instance) -> dict:
    cands, blocks, no_data = collect(instance)
    busy = _open_pages(instance)
    out = []
    for c in cands.values():
        c["in_queue"] = c["url"] in busy
        out.append(c)
    out.sort(key=lambda c: (c["in_queue"], -len(c["reasons"]),
                            -max((r.get("impressions") or 0) for r in c["reasons"]), c["url"]))
    line = store.write_json(instance.root / "data" / "refresh.json", "refresh", out,
                            meta={"date": instance.today.isoformat(), "blocks": blocks, "no_data": no_data})
    return {"candidates": out, "blocks": blocks, "no_data": no_data, "write": line}


def _queries(instance, url: str) -> list[str]:
    """Запросы страницы для выдачи: зона дожима последнего снимка, затем целевые ключи карты спроса."""
    rows = store.read_jsonl(instance.file("positions"), missing_ok=True)
    last = max((x["date"] for x in rows), default="")
    zone = sorted((x for x in rows if x["date"] == last and norm_url(x["url"]) == url and x.get("position")),
                  key=lambda x: -(x.get("impressions") or 0))
    out = [x["query"] for x in zone]
    try:
        pages = store.read_json(instance.file("demand_map"), "demand_map", missing_ok=True)["records"]
    except EngineError:
        pages = []
    for pg in pages:
        if norm_url(pg["url"]) == url:
            out += list(pg.get("target_keys") or [])
    return list(dict.fromkeys(q.strip().lower() for q in out if q.strip()))[:FORMULATIONS]


def _direction(instance, url: str) -> str:
    from .demand_map import direction_of
    for item in instance.index().pages:
        if norm_url(item.page.url) == url:
            return direction_of(instance, instance.norm.key(item.page.title)) or "без направления"
    return "без направления"


def baseline_path(instance, tid: str) -> Path:
    return instance.root / "data" / "refresh" / "baseline" / f"{tid}-{instance.today.isoformat()}.json"


def save_baseline(instance, tid: str, url: str) -> dict:
    """Текст страницы с сайта сейчас — отдельным файлом темы; существующий файл не перезаписывается."""
    from ..core.htmldoc import HtmlDoc
    from .prepare import _fetcher
    try:
        got = _fetcher(instance, {url}).get(url)
    except SourceError as exc:
        raise EngineError(f"не проверено: {url} — сайт не ответил ({exc}); эталон не снят, тема не создана") from exc
    if got.status != 200:
        raise GateRefused(f"{url}: HTTP {got.status} — страница не живая, правка не ставится")
    m = HtmlDoc(got.text, instance.p["prepare"]["main_blocks"]).markers()
    if m["chars"] == 0:
        raise GateRefused(f"{url}: текст страницы не разобран — эталон пуст, правка не ставится")
    text = m.pop("text")
    sha = _digest({"url": url, "day": instance.today.isoformat(), "markers": m, "text": text})
    path = baseline_path(instance, tid)
    if path.exists():
        raise GateRefused(f"эталон {path.name} уже снят сегодня — повторно не перезаписывается")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "day": instance.today.isoformat(), "sha256": sha, "markers": m,
                                "text": text}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return {"path": str(path), "sha256": sha}


def _digest(body: dict) -> str:
    """Сумма всего эталона — адрес, день, маркеры и текст: правка любого поля видна."""
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def baseline_of(instance, topic: dict) -> dict:
    """Эталон темы со сверкой суммы: подменённый или правленый файл — ошибка, а не тихое сравнение не с тем."""
    ref = topic.get("baseline") or {}
    path = Path(ref.get("path", ""))
    if not ref or not path.is_file():
        raise EngineError(f"тема {topic['id']}: эталона страницы нет — `refresh take` заново")
    data = json.loads(path.read_text(encoding="utf-8"))
    body = {k: data.get(k) for k in ("url", "day", "markers", "text")}
    if _digest(body) != ref["sha256"] or data.get("sha256") != ref["sha256"]:
        raise EngineError(f"тема {topic['id']}: эталон {path.name} не совпал с суммой темы — файл изменён")
    return data


def change_share(baseline_text: str, draft_text: str) -> float:
    """Доля восьмисловных цепочек черновика, которых нет в эталоне (Т36.4)."""
    from .gate_article import _shingles
    new = _shingles(draft_text)
    if not new:
        return 0.0
    return len(new - _shingles(baseline_text)) / len(new)


def take(instance, url: str, max_quota: int | None = None) -> dict:
    from . import dedup
    url = norm_url(url)
    slug = slug_of(url)
    if not slug:
        raise GateRefused(f"{url}: адрес без слага — правка не ставится")
    tid = f"refresh-{slug}"
    r = instance.p["refresh"]
    with locks.held(instance, "refresh", "write"):
        topics = queue_mod.load(instance)
        if any(t.get("type") == TYPE and norm_url(t["page"]) == url and t["status"] in OPEN for t in topics):
            raise GateRefused(f"правка {url} уже в очереди — повторно не ставится")
        since = (instance.today - timedelta(days=7)).isoformat()
        week = [t for t in topics if t.get("type") == TYPE and (t.get("taken") or "") > since]
        if len(week) >= r["weekly_limit"]:
            raise GateRefused(f"за 7 дней взято правок {len(week)} — предел refresh.weekly_limit {r['weekly_limit']}")
        queries = _queries(instance, url)
        if not queries:
            raise GateRefused(f"{url}: запросов страницы нет ни в позициях, ни в карте спроса — выдачу не с чем снять")
        topic_id = tid if not any(t["id"] == tid for t in topics) else f"{tid}-{instance.today:%Y%m%d}"
        ctx = dedup.Context(instance, fresh=True, max_quota=max_quota)
        try:
            serp = ctx.serp(queries)
        finally:
            queue_mod._spend_objects(instance, topic_id, ctx)
        if not serp or any(q not in base for base in serp.values() for q in queries):
            raise GateRefused(f"{url}: выдачи на сегодня по всем запросам нет (квота) — правка не взята")
        baseline = save_baseline(instance, topic_id, url)     # эталон — последним: отказ выше файла не оставляет
        topic = {"id": topic_id, "type": TYPE, "page": url, "slug": slug, "topic": queries[0],
                 "formulations": queries, "direction": _direction(instance, url), "status": queue_mod.TAKEN,
                 "taken": instance.today.isoformat(), "baseline": baseline, "cluster_id": None}
        topics.append(topic)
        line = queue_mod.save_serp(instance, topic_id, queries)
        write = queue_mod.save(instance, topics)
    return {"id": topic_id, "taken": True, "queries": queries, "baseline": baseline["path"],
            "write": f"{write}; снимок выдачи — {line}"}


def is_refresh(topic: dict | None) -> bool:
    return bool(topic) and topic.get("type") == TYPE


def render_scan(out: dict) -> str:
    lines = ["Дожим и актуализация — кандидаты на правку"]
    for name, title in (("zone", "зона дожима"), ("age", "старые статьи"), ("ai", "ИИ-видимость"),
                        ("behavior", "поведение")):
        b = out["blocks"][name]
        lines.append(f"  {title}: {b['status']}" + (f" — {b['why']}" if b.get("why") else
                                                    f", найдено {b.get('found', 0)}"))
    for c in out["candidates"]:
        flag = " (уже в очереди)" if c["in_queue"] else ""
        lines.append(f"- {c['url']}{flag}")
        lines += [f"    {r['note']}" for r in c["reasons"]]
    if out["no_data"]:
        lines.append(f"нет данных по {len(out['no_data'])} строкам (см. data/refresh.json)")
    lines.append("дальше: `content-engine refresh take <адрес>` — не больше refresh.weekly_limit в неделю")
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("refresh", help="дожим и актуализация: scan | take")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("scan", help="кандидаты на правку страниц без сети")
    add_profile(p)
    p.add_argument("--unattended", action="store_true", help="запуск по расписанию")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("take", help="правку страницы — в очередь: эталон и снимок выдачи")
    add_profile(p)
    p.add_argument("url")
    from ..core.budget import add_flags
    add_flags(p)                                    # запасной источник выдачи платный (Н6)
    p.add_argument("--max-quota", type=int, help="объектов квоты на прогон, не больше")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "scan":
        out = scan(instance)
        print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else render_scan(out) + "\n" + out["write"])
        return 0
    out = take(instance, args.url, args.max_quota)
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else
          f"Правка {out['id']} взята: запросы {', '.join(out['queries'])}; эталон {out['baseline']}; {out['write']}\n"
          f"дальше: `content-engine prepare top {out['id']}`")
    return 0
