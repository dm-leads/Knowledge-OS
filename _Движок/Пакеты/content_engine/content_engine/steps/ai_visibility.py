"""ИИ-видимость (Д5, Т37): `content-engine ai score | citations`.

- `score [--offline]` — пригодность статей корпуса к цитированию, балл кодом по признакам: прямой ответ в начале
  (первый абзац — `ai_visibility.answer_words` слов), разделы H2 цитируемыми кусками (не длиннее `section_max_words`),
  конкретика в первой трети (число), свежая дата (не старше года), источник (внешняя ссылка), ИИ-краулеры не закрыты
  в `robots.txt` своего домена. Последний признак — один GET; с `--offline` или при ошибке сайта — «нет данных», и балл
  считается из пяти с пометкой. Итог — `data/ai_visibility.jsonl`; ниже `min_score` — кандидат дожима (`refresh scan`).
- `citations [--max-quota N]` — цитирование сайта (Т37.2) по `ai_visibility.citations`: `none` — «не подключено»;
  `keysso` — отчёт ответов Алисы Keys.so (база Москвы): только запросы, в ответах на которые сайт — среди источников;
  `keysso_tracker` — чтение трекера нейросетей, созданного человеком (В16). Квота — не деньги, но с пределом до запроса:
  `--max-quota` или `ai_visibility.max_quota`; предела нет — отказ без запроса (с `--unattended` — только профиль).
  Снимок `data/ai_citations/<день>.json`; динамика — против прошлого снимка, с интервалом в днях. Обзоры поисковиков —
  источника нет, «не подключено».
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import unquote

from ..core import store
from ..core.errors import GateRefused, SourceError, source_failures
from ..core.number import NO_DATA
from ..core.urls import host_unicode, is_own, norm_url

FEATURES = ("answer", "sections", "specifics", "date", "source", "bots")
NAMES = {"answer": "прямой ответ в начале", "sections": "разделы цитируемыми кусками", "specifics":
         "конкретика в первой трети", "date": "свежая дата", "source": "источник", "bots": "ИИ-краулеры открыты"}
LINK = re.compile(r"https?://([^/\s)\]>\"']+)", re.IGNORECASE)
FRESH_DAYS = 365


def blocked_bots(robots: str, bots: list[str]) -> list[str]:
    """ИИ-краулеры, которым `robots.txt` закрывает весь сайт: своя группа или общая `*` без своей группы."""
    groups, agents, rules, prev_agent = [], [], [], False
    for raw in robots.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not prev_agent and agents:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            prev_agent = True
        elif key in ("disallow", "allow"):
            rules.append((key, value))
            prev_agent = False
    if agents:
        groups.append((agents, rules))

    def closed(rules) -> bool:
        return any(k == "disallow" and v == "/" for k, v in rules) and not any(k == "allow" and v == "/" for k, v in rules)
    star = [r for a, r in groups if "*" in a]
    out = []
    for bot in bots:
        own = [r for a, r in groups if bot.lower() in a]
        rules = own[0] if own else (star[0] if star else [])
        if closed(rules):
            out.append(bot)
    return out


def _robots(instance, offline: bool) -> dict:
    if offline:
        return {"status": NO_DATA, "why": "--offline: robots.txt не читался"}
    from ..adapters.page_fetch import PageFetcher
    domain = instance.p["site"]["domains"][0]
    url = f"https://{domain}/robots.txt"
    pr = instance.p["prepare"]
    fetcher = PageFetcher(allowed_urls={url}, timeout=pr["page_timeout"], max_kb=pr["page_max_kb"],
                          transport=instance.transport)
    try:
        got = fetcher.get(url)
    except SourceError as exc:
        return {"status": NO_DATA, "why": f"robots.txt не ответил: {exc}"}
    if got.status == 404:
        return {"status": "ок", "blocked": [], "why": "robots.txt нет — краулеры не закрыты"}
    if got.status != 200:
        return {"status": NO_DATA, "why": f"robots.txt: HTTP {got.status}"}
    return {"status": "ок", "blocked": blocked_bots(got.text, instance.p["ai_visibility"]["bots"])}


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip() and not p.strip().startswith("#")]


def page_features(instance, page) -> dict:
    """Признаки одной статьи корпуса: True, False или None («нет данных»)."""
    a = instance.p["ai_visibility"]
    lo, hi = a["answer_words"]
    first = next((p for s in page.sections for p in _paragraphs(s.text)), "")
    n = len(re.findall(r"\w+", first))
    h2 = [s for s in page.sections if s.heading]
    longest = max((len(re.findall(r"\w+", s.text)) for s in h2), default=0)
    text = page.text
    third = text[:max(1, len(text) // 3)]
    own = instance.p["site"]["domains"]
    hosts = {host_unicode(unquote(h)) for h in LINK.findall(text)}
    fresh = None
    if page.date:
        fresh = (instance.today - date.fromisoformat(page.date)).days <= FRESH_DAYS
    return {"answer": lo <= n <= hi, "sections": len(h2) >= 2 and longest <= a["section_max_words"],
            "specifics": bool(re.search(r"\d", third)), "date": fresh,
            "source": any(not is_own(f"https://{h}/", own) for h in hosts)}


def score(instance, *, offline: bool = False) -> dict:
    robots = _robots(instance, offline)
    bots_ok = None if robots["status"] != "ок" else not robots["blocked"]
    rows = []
    for item in instance.index().pages:
        page = item.page
        if page.kind != "article":
            continue
        f = page_features(instance, page)
        f["bots"] = bots_ok
        known = {k: v for k, v in f.items() if v is not None}
        rows.append({"date": instance.today.isoformat(), "url": norm_url(page.url), "score": sum(known.values()),
                     "max": len(known), "missing": [NAMES[k] for k, v in known.items() if not v],
                     "no_data": [NAMES[k] for k, v in f.items() if v is None]})
    path = instance.root / "data" / "ai_visibility.jsonl"
    old = [r for r in store.read_jsonl(path, missing_ok=True) if r["date"] != instance.today.isoformat()]
    line = store.write_jsonl(path, old + rows, source_count=len(old) + len(rows))
    floor = instance.p["ai_visibility"]["min_score"]
    return {"rows": rows, "robots": robots, "weak": [r for r in rows if r["score"] < floor], "write": line}


def _limit(instance, max_quota: int | None) -> int:
    a = instance.p["ai_visibility"]
    if getattr(instance, "unattended", False) or max_quota is None:
        return a["max_quota"]
    return max_quota


def _cited(urls, domains) -> list[str]:
    """Наши адреса среди источников ответа: адреса приходят и в процентной кодировке — раскодируются до сверки."""
    return [norm_url(unquote(str(u))) for u in urls or [] if is_own(norm_url(unquote(str(u))), domains)]


def _previous(instance) -> dict | None:
    folder = instance.root / "data" / "ai_citations"
    days = sorted(p.stem for p in folder.glob("*.json") if p.stem < instance.today.isoformat()) if folder.is_dir() else []
    return store.read_json(folder / f"{days[-1]}.json", "ai_citations") if days else None


def citations(instance, *, max_quota: int | None = None, transport=None) -> dict:
    from ..adapters.keysso import KeyssoAi
    from ..core.env_file import secret
    a = instance.p["ai_visibility"]
    kind = a["citations"]
    if kind == "none":
        return {"status": "не подключено", "why": "ai_visibility.citations: none — подключить: keysso (отчёт ответов "
                                                   "Алисы, квота) или keysso_tracker (трекер, создаёт человек)",
                "overviews": "не подключено: источника обзоров поисковиков нет"}
    limit = _limit(instance, max_quota)
    pages = min(limit, a["max_pages"]) if kind == "keysso" else min(limit, 1)
    if pages <= 0:
        raise GateRefused("квота Keys.so на цитирование не разрешена: --max-quota или ai_visibility.max_quota — "
                          "запроса нет")
    key = secret(instance.p, instance.p["sources"]["keysso"]["key_env"])
    client = KeyssoAi(key, tracker_id=a["tracker_id"], transport=transport if transport is not None
                      else instance.transport)
    domains = instance.p["site"]["domains"]
    out = {"date": instance.today.isoformat(), "via": kind, "planned_requests": pages,
           "overviews": "не подключено: источника обзоров поисковиков нет"}
    try:
        if kind == "keysso":
            got = client.answers(domains[0], pages=pages)
            rows = [{"query": r.get("word"), "ws": r.get("ws"), "ours": _cited(r.get("source_urls"), domains),
                     "created_at": r.get("created_at")} for r in got["rows"]]
            cited = [r for r in rows if r["ours"]]
            pages_count: dict[str, int] = {}
            for r in cited:
                for u in set(r["ours"]):
                    pages_count[u] = pages_count.get(u, 0) + 1
            out.update(status="ок", total=got["total"], fetched=len(rows), cited=len(cited),
                       requests=got["requests"], pages=sorted(pages_count.items(), key=lambda x: -x[1])[:50],
                       top=cited[:20])
        else:
            data = client.tracker_citation()
            out.update(status="ок" if data.get("percent") is not None else NO_DATA, requests=1,
                       percent=data.get("percent"), delta_percent=data.get("delta_percent"),
                       total_matched=data.get("total_matched"), total_with_sources=data.get("total_with_sources"),
                       chart=data.get("chart") or {})
    except source_failures() as exc:
        return {**out, "status": "источник не ответил", "why": str(exc)}
    prev = _previous(instance)
    if prev and out["status"] == "ок":
        meta = prev
        days = (instance.today - date.fromisoformat(meta["date"])).days
        field = "total" if kind == "keysso" else "percent"
        if meta.get(field) is not None and out.get(field) is not None:
            out["dynamics"] = {"field": field, "was": meta[field], "now": out[field], "days": days,
                               "note": f"{field}: {meta[field]} → {out[field]} за {days} дн."}
    folder = instance.root / "data" / "ai_citations"
    out["write"] = store.write_json(folder / f"{instance.today.isoformat()}.json", "ai_citations", out.get("top", []),
                                    meta={k: v for k, v in out.items() if k != "top"})
    return out


def render_score(out: dict) -> str:
    r = out["robots"]
    lines = ["ИИ-видимость: пригодность к цитированию",
             "  robots.txt: " + (f"закрыты {', '.join(r['blocked'])}" if r.get("blocked") else
                                 ("краулеры открыты" if r["status"] == "ок" else f"нет данных — {r['why']}"))]
    rows = out["rows"]
    if rows:
        lines.append(f"  статей {len(rows)}, ниже порога {len(out['weak'])}")
    for x in sorted(out["weak"], key=lambda x: x["score"])[:30]:
        lines.append(f"- {x['url']}: {x['score']} из {x['max']} — нет: {', '.join(x['missing'])}")
    lines.append(out["write"])
    return "\n".join(lines)


def render_citations(out: dict) -> str:
    if out["status"] == "не подключено":
        return f"Цитирование: не подключено — {out['why']}\nОбзоры поисковиков: {out['overviews']}"
    if out["status"] != "ок":
        return f"Цитирование: {out['status']} — {out.get('why', '')}"
    if out["via"] == "keysso":
        lines = [f"Цитирование в ответах Алисы (Keys.so, база Москвы): запросов с сайтом в источниках — {out['total']} "
                 f"(факт отчёта на {out['date']}); разобрано {out['fetched']}, запросов к API {out['requests']}"]
        lines += [f"- {u}: {n}" for u, n in out["pages"][:15]]
    else:
        lines = [f"Цитирование по трекеру: {out.get('percent')} % ответов с источниками (изменение "
                 f"{out.get('delta_percent')} п.)"]
    if out.get("dynamics"):
        lines.append(f"динамика: {out['dynamics']['note']}")
    lines.append(f"Обзоры поисковиков: {out['overviews']}")
    lines.append(out["write"])
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("ai", help="ИИ-видимость: score | citations")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("score", help="балл пригодности статей к цитированию")
    add_profile(p)
    p.add_argument("--offline", action="store_true", help="без robots.txt — признак «нет данных»")
    p.add_argument("--unattended", action="store_true", help="запуск по расписанию")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("citations", help="цитирование сайта в ответах ИИ (Keys.so)")
    add_profile(p)
    p.add_argument("--max-quota", type=int, help="запросов Keys.so на прогон, не больше")
    p.add_argument("--unattended", action="store_true", help="запуск по расписанию: предел — только из профиля")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "score":
        out = score(instance, offline=args.offline)
        print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else render_score(out))
        return 0
    out = citations(instance, max_quota=args.max_quota)
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else render_citations(out))
    return 0 if out["status"] in ("ок", "не подключено") else 3
