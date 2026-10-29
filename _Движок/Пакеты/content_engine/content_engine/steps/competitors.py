"""Разведка конкурентов (А4, Т41.2): `content-engine competitors snap | pages`. Разведка устройства их статей и темпа
публикаций, а не слежка за позициями. Только чтение, белый список — домены `search.competitors`.

- `snap` — карты сайтов конкурентов (адрес карты — из `robots.txt`, иначе `/sitemap.xml`; индекс карт обходится вглубь)
  → снимок `data/competitors/<день>.json`: домен → адреса с `lastmod`. Новые адреса — против ближайшего снимка старше
  `competitors.window_days`: «появились между днём прошлого снимка и сегодня». Без `lastmod` день публикации неизвестен —
  статус proxy; с `lastmod` внутри промежутка — «оценка», если `lastmod` у домена не одинаковый у большинства адресов
  (массовая простановка даты — признак ненадёжности, тогда proxy). Темп — новых адресов на 7 дней промежутка. Первого
  снимка нет — «нет данных» с днём, когда сравнение станет возможно.
- `pages [--limit N]` — разбор новых адресов последнего сравнения (не больше `max_pages`, пауза `delay_seconds`) теми же
  маркерами, что разбор топа: разделы, объём, картинки, таблицы, списки, разметка. Итог — `data/competitors/pages-<день>.json`.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import date, timedelta

from ..core import store
from ..core.errors import EndpointNotAllowed, GateRefused, SourceError
from ..core.number import ESTIMATE, NO_DATA, PROXY
from ..core.urls import host_unicode, norm_url

SLEEP = time.sleep
LOC = re.compile(r"<(url|sitemap)>(.*?)</\1>", re.S | re.I)
TAG = {name: re.compile(rf"<{name}>\s*(.*?)\s*</{name}>", re.S | re.I) for name in ("loc", "lastmod")}
MAX_MAPS = 50
SITEMAP_KB = 20480
UNRELIABLE_SHARE = 0.5


def _folder(instance):
    return instance.root / "data" / "competitors"


def _fetcher(instance, domains):
    from ..adapters.page_fetch import PageFetcher
    pr = instance.p["prepare"]
    return PageFetcher(allowed_hosts=domains, timeout=pr["page_timeout"], max_kb=SITEMAP_KB,
                       transport=instance.transport)


def _get(fetcher, url: str) -> str:
    got = fetcher.get(url)
    if got.status != 200:
        raise SourceError(f"{url}: HTTP {got.status}")
    return got.text


def parse_map(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Вид карты (`urlset` или `index`) и пары (адрес, lastmod)."""
    kind = "index" if "<sitemapindex" in text.lower() else "urlset"
    out = []
    for _, block in LOC.findall(text):
        loc = TAG["loc"].search(block)
        if not loc:
            continue
        mod = TAG["lastmod"].search(block)
        out.append((loc.group(1).strip(), mod.group(1).strip()[:10] if mod else ""))
    return kind, out


def read_domain(instance, fetcher, domain: str) -> dict:
    try:
        robots = _get(fetcher, f"https://{domain}/robots.txt")
        maps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
    except SourceError:
        maps = []
    queue = list(dict.fromkeys(maps or [f"https://{domain}/sitemap.xml"]))
    urls: dict[str, str] = {}
    seen, skipped = 0, []
    while queue and seen < MAX_MAPS:
        url = queue.pop(0)
        seen += 1
        if url.endswith(".gz"):
            skipped.append(f"{url}: сжатая карта не читается")
            continue
        try:
            text = _get(fetcher, url)
        except EndpointNotAllowed:
            skipped.append(f"{url}: карта вне домена конкурента — не читается")
            continue
        kind, pairs = parse_map(text)
        if kind == "index":
            queue += [u for u, _ in pairs]
        else:
            for u, mod in pairs:
                urls[norm_url(u)] = mod
    if queue:
        skipped.append(f"карт больше {MAX_MAPS} — {len(queue)} не прочитаны")
    return {"domain": domain, "status": "ок" if not skipped else "неполный", "maps": seen, "urls": urls,
            "skipped": skipped}


def snap(instance) -> dict:
    domains = [host_unicode(d) for d in instance.p["search"]["competitors"]]
    if not domains:
        return {"status": "не подключено", "why": "search.competitors пуст — впишите домены конкурентов"}
    fetcher = _fetcher(instance, domains)
    records = []
    for i, domain in enumerate(domains):
        if i:
            SLEEP(instance.p["competitors"]["delay_seconds"])
        try:
            records.append(read_domain(instance, fetcher, domain))
        except SourceError as exc:
            records.append({"domain": domain, "status": "источник не ответил", "why": str(exc), "urls": {}})
    path = _folder(instance) / f"{instance.today.isoformat()}.json"
    line = store.write_json(path, "competitors", records, meta={"date": instance.today.isoformat()})
    out = {"status": "ок", "domains": len(domains), "write": line}
    out.update(compare(instance))
    return out


def _snapshots(instance) -> list[str]:
    folder = _folder(instance)
    return sorted(p.stem for p in folder.glob("20*.json")) if folder.is_dir() else []


def compare(instance) -> dict:
    days = _snapshots(instance)
    window = instance.p["competitors"]["window_days"]
    today = instance.today.isoformat()
    if today not in days:
        return {"compare": {"status": NO_DATA, "why": "снимка за сегодня нет — `competitors snap`"}}
    edge = (instance.today - timedelta(days=window)).isoformat()
    older = [d for d in days if d <= edge]
    if not older:
        first = days[0]
        ready = date.fromisoformat(first) + timedelta(days=window)
        return {"compare": {"status": NO_DATA, "why": f"снимка старше {window} дн. нет (первый — {first}); сравнение "
                                                      f"станет возможно с {ready.isoformat()}"}}
    base_day = older[-1]
    now = {r["domain"]: r for r in store.read_json(_folder(instance) / f"{today}.json", "competitors")["records"]}
    was = {r["domain"]: r for r in store.read_json(_folder(instance) / f"{base_day}.json", "competitors")["records"]}
    interval = (instance.today - date.fromisoformat(base_day)).days
    rows = []
    for domain, r in now.items():
        old = was.get(domain)
        if r["status"] != "ок" or not old or old.get("status") != "ок":
            why = r.get("why") or ("; ".join(r.get("skipped") or []) if r["status"] == "неполный" else "") \
                or ("прошлый снимок неполный" if old else "в прошлом снимке домена нет")
            rows.append({"domain": domain, "status": NO_DATA, "why": why})
            continue
        new = sorted(set(r["urls"]) - set(old["urls"]))
        mods = [m for m in r["urls"].values() if m]
        common = Counter(mods).most_common(1)
        reliable = bool(mods) and (common[0][1] / len(mods) if common else 1) < UNRELIABLE_SHARE
        status = ESTIMATE if reliable else PROXY
        inside = [u for u in new if r["urls"][u] and base_day < r["urls"][u] <= today] if reliable else []
        rows.append({"domain": domain, "status": status, "new": len(new), "new_urls": new[:200],
                     "lastmod_inside": len(inside), "pace_per_week": round(len(new) / interval * 7, 1),
                     "why": ("lastmod надёжен: дата публикации — оценка" if reliable else
                             "lastmod нет или одинаковый у большинства адресов — известен только промежуток")})
    return {"compare": {"status": "ок", "from": base_day, "to": today, "days": interval, "rows": rows}}


def pages(instance, *, limit: int | None = None) -> dict:
    from ..core.htmldoc import HtmlDoc
    cmp = compare(instance)["compare"]
    if cmp["status"] != "ок":
        raise GateRefused(f"новых адресов нет: {cmp['why']}")
    c = instance.p["competitors"]
    if limit is not None and limit < 1:
        raise GateRefused(f"--limit {limit}: страниц за прогон — от 1")
    cap = c["max_pages"] if limit is None else min(limit, c["max_pages"])
    todo = [(r["domain"], u) for r in cmp["rows"] if r.get("new_urls") for u in r["new_urls"]][:cap]
    from ..adapters.page_fetch import PageFetcher
    pr = instance.p["prepare"]
    fetcher = PageFetcher(allowed_hosts=[host_unicode(d) for d in instance.p["search"]["competitors"]],
                          timeout=pr["page_timeout"], max_kb=pr["page_max_kb"], transport=instance.transport)
    out = []
    for n, (domain, url) in enumerate(todo):
        if n:
            SLEEP(c["delay_seconds"])
        try:
            got = fetcher.get(url if "://" in url else f"https://{url}")
        except SourceError as exc:
            out.append({"url": url, "domain": domain, "status": "не загружено", "why": str(exc)})
            continue
        if got.status != 200:
            out.append({"url": url, "domain": domain, "status": "не загружено", "why": f"HTTP {got.status}"})
            continue
        doc = HtmlDoc(got.text, pr["main_blocks"])
        m = doc.markers()
        types = sorted({str(x.get("@type")) for x in doc.schema_nodes() if x.get("@type")})
        out.append({"url": url, "domain": domain, "status": "разобрано", "h2": len(m["h2"]), "h3": len(m["h3"]),
                    "chars": m["chars"], "images": m["images"], "tables": m["tables"], "lists": m["lists"],
                    "faq": m["faq"], "schema": types, "headings": m["h2"][:20]})
    line = store.write_json(_folder(instance) / f"pages-{instance.today.isoformat()}.json", "competitor_pages", out,
                            meta={"date": instance.today.isoformat(), "from": cmp["from"]})
    return {"pages": out, "write": line}


def render_snap(out: dict) -> str:
    if out["status"] != "ок":
        return f"Конкуренты: {out['status']} — {out['why']}"
    cmp = out["compare"]
    lines = [f"Конкуренты: снято доменов {out['domains']}; {out['write']}"]
    if cmp["status"] != "ок":
        lines.append(f"новые адреса: {cmp['status']} — {cmp['why']}")
        return "\n".join(lines)
    lines.append(f"новые адреса между {cmp['from']} и {cmp['to']} ({cmp['days']} дн.):")
    for r in cmp["rows"]:
        if r["status"] == NO_DATA:
            lines.append(f"- {r['domain']}: нет данных — {r['why']}")
        else:
            lines.append(f"- {r['domain']}: {r['new']} ({r['status']}), темп {r['pace_per_week']} в неделю — {r['why']}")
    lines.append("дальше: `content-engine competitors pages` — устройство новых статей")
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("competitors", help="разведка конкурентов: snap | pages")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("snap", help="снимок карт сайтов конкурентов и новые адреса")
    add_profile(p)
    p.add_argument("--unattended", action="store_true", help="запуск по расписанию")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("pages", help="разбор устройства новых статей конкурентов")
    add_profile(p)
    p.add_argument("--limit", type=int, help="страниц за прогон, не больше competitors.max_pages")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "snap":
        out = snap(instance)
        print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else render_snap(out))
        return 0
    out = pages(instance, limit=args.limit)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for p in out["pages"]:
            print(f"- {p['url']}: " + (f"H2 {p['h2']}, {p['chars']} знаков, картинок {p['images']}, таблиц "
                                       f"{p['tables']}, FAQ {'да' if p['faq'] else 'нет'}, разметка "
                                       f"{', '.join(p['schema']) or 'нет'}" if p["status"] == "разобрано"
                                       else f"{p['status']} — {p['why']}"))
        print(out["write"])
    return 0
