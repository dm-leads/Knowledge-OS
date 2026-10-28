"""Карта спроса (А3, Т8) и пробелы с направлениями (А4, Т9): `content-engine map build`.

Страницы — из корпуса (статьи) и продающих разделов (карта сайта, список профиля, адреса из источников). Запросы
страниц — из отчёта Keys.so по своему домену и из Search Console (страница × запрос), уже собранные `demand collect`.
Владелец кластера — наша страница с лучшей позицией, при равенстве — с большими показами. Две и больше наших страниц
в топ-10 по запросам кластера — «пересечение живых». Статусы кластера: free, reserved, covered, commercial, frozen.
У страницы: тип, смысловой ключ (заголовок и подзаголовки), целевые ключи (запросы кластеров, которыми она владеет
в топ-10),
до пяти головных запросов; выдача головных запросов снимается через квоту и кэш — по ней работает слой 3 гейта.
Сверка с картой сайта — по slug без учёта раздела.
"""
from __future__ import annotations

import json
from collections import defaultdict

from ..adapters.corpus import reconcile, sitemap_urls
from ..adapters.serp import SiteKeyword
from ..adapters.value import PageValue
from ..core import store
from ..core.errors import SourceError
from ..core.normalize import Key
from ..core.quota import fetch_serp
from ..core.urls import host_unicode, is_own, norm_url, section_of, slug_of
from . import cluster as cluster_mod
from . import demand as demand_mod
from . import reservations

FREE, RESERVED, COVERED, COMMERCIAL, FROZEN = "free", "reserved", "covered", "commercial", "frozen"
ARTICLE, SALES, OTHER = "article", "sales", "other"
HEAD_QUERIES = 5


def page_identity(instance, url: str, corpus_slugs: set[str]) -> tuple[str, str]:
    """(id страницы, тип): статья — по slug (статьи переезжают между разделами), остальное — по адресу."""
    site = instance.p["site"]
    normal = norm_url(url)
    sales = {norm_url(u) for u in site["sales_urls"]}
    if normal in sales or section_of(normal) in site["sales_sections"]:
        return normal, SALES
    slug = slug_of(normal)
    if slug in corpus_slugs or section_of(normal) in site["article_sections"]:
        return slug, ARTICLE
    return normal, OTHER


def commercial_count(instance, urls) -> int:
    shops = [host_unicode(d) for d in instance.p["search"]["commercial_domains"]]
    hosts = {u.split("/", 1)[0] for u in urls}
    return sum(1 for h in hosts if any(h == s or h.endswith("." + s) for s in shops))


def direction_of(instance, key: Key) -> str:
    for d in instance.p["directions"]:
        for word in d["words"]:
            wk = instance.norm.key(word)
            if wk and wk.similarity(key) == 1.0:
                return d["name"]
    return "без направления"


def build_map(instance, *, offline: bool = False, max_quota: int | None = None) -> dict:
    p = instance.p
    pages_corpus, corpus_report = instance.corpus()
    corpus_slugs = {pg.slug for pg in pages_corpus}
    rows = demand_mod.load(instance)
    clusters = cluster_mod.load(instance)
    if not clusters:
        raise SourceError("кластеров нет: сначала `content-engine cluster`")
    index = instance.index()
    pages: dict[str, dict] = {}
    for item in index.pages:
        pg = item.page
        pages[pg.slug] = {"id": pg.slug, "url": pg.url, "type": ARTICLE, "title": pg.title, "corpus_id": pg.id,
                          "key": item.key.to_list(), "title_key": item.title_key.to_list(), "target_keys": [],
                          "head_queries": [], "status": "live"}
    # статьи из записей выпуска, которых ещё нет в корпусе, — иначе пересборка стёрла бы их из карты (Т23, Р17)
    from .records import release_pages
    for page in release_pages(instance, set(pages)):
        pages[page["id"]] = page
    sitemap, sitemap_error = [], ""
    try:
        sitemap = sitemap_urls(p, instance.root, transport=instance.transport)
    except SourceError as exc:
        sitemap_error = str(exc)
    for url in sitemap + [norm_url(u) for u in p["site"]["sales_urls"]]:
        pid, kind = page_identity(instance, url, corpus_slugs)
        if kind == SALES and pid not in pages:
            pages[pid] = {"id": pid, "url": pid, "type": SALES, "title": "", "corpus_id": "", "key": [],
                          "title_key": [], "target_keys": [], "head_queries": [], "status": "live"}
    # позиции наших страниц по запросам
    by_query: dict[str, list[dict]] = defaultdict(list)
    page_queries: dict[str, list[tuple[float, str]]] = defaultdict(list)
    site_keywords: list[SiteKeyword] = []
    for row in rows:
        for hit in row["pages"]:
            if not is_own(hit["url"], p["site"]["domains"]):
                continue
            pid, kind = page_identity(instance, hit["url"], corpus_slugs)
            if pid not in pages:
                pages[pid] = {"id": pid, "url": norm_url(hit["url"]), "type": kind, "title": "", "corpus_id": "",
                              "key": [], "title_key": [], "target_keys": [], "head_queries": [], "status": "live"}
            by_query[row["query"]].append({"page": pid, "position": hit["position"],
                                           "impressions": hit["impressions"] or 0})
            page_queries[pid].append((demand_mod.weight(row), row["query"]))
            if hit.get("bid") is not None:
                site_keywords.append(SiteKeyword(pages[pid]["url"], row["query"], int(hit["position"] or 0),
                                                 int(hit.get("frequency") or 0), hit["bid"]))
    for pid, items in page_queries.items():
        seen = []
        for _, q in sorted(items, key=lambda x: (-x[0], x[1])):
            if q not in seen:
                seen.append(q)
        pages[pid]["head_queries"] = seen[:HEAD_QUERIES]
    # кластеры: владелец, статусы, пересечения живых
    frozen = {r["cluster_id"]: r["reason"] for r in
              store.read_json(instance.file("frozen"), "frozen", missing_ok=True)["records"]}
    reserved = {r["cluster_id"]: r["id"] for r in reservations.active(instance) if r["cluster_id"]}
    value = PageValue(p, instance.root, instance.today, site_keywords=site_keywords)
    live_overlaps, map_clusters = [], []
    for c in clusters:
        positions: dict[str, dict] = {}
        for q in c["queries"]:
            for hit in by_query.get(q["query"], []):
                pos = hit["position"] or 999
                best = positions.get(hit["page"])
                if best is None or pos < best["position"] or (pos == best["position"]
                                                              and hit["impressions"] > best["impressions"]):
                    positions[hit["page"]] = {"position": pos, "impressions": hit["impressions"]}
        owner = min(positions, key=lambda k: (positions[k]["position"], -positions[k]["impressions"], k),
                    default=None)
        top10 = sorted(k for k, v in positions.items() if v["position"] <= 10)
        released = next((pid for pid, pg in pages.items() if pg.get("source") == "запись выпуска"
                         and pg.get("cluster_id") == c["id"]), None)
        if released and not owner:
            owner = released
        if c["id"] in frozen:
            status = FROZEN
        elif c["id"] in reserved:
            status = RESERVED
        elif owner and pages[owner]["type"] == SALES:
            status = COMMERCIAL
        elif commercial_count(instance, c["head_top"]) >= p["thresholds"]["commercial_min"]:
            status = COMMERCIAL
        elif owner:
            status = COVERED
        else:
            status = FREE
        if len(top10) >= 2:
            live_overlaps.append({"cluster_id": c["id"], "head": c["head"], "pages": top10})
        if owner and owner in positions and positions[owner]["position"] <= 10:
            # целевые ключи — только кластеры, которые страница держит в топ-10: владение на 50-й позиции делало
            # целевыми общие запросы, и гейт статьи запрещал их всем (сверка с ручной приёмкой, 25.10.2026)
            pages[owner]["target_keys"].extend(q["query"] for q in c["queries"])
        map_clusters.append({"id": c["id"], "head": c["head"], "status": status, "owner": owner,
                             "positions": positions, "live_overlap": len(top10) >= 2, "weight": c["weight"],
                             "frozen_reason": frozen.get(c["id"], ""), "reservation": reserved.get(c["id"])})
    # выдача головных запросов страниц — для слоя 3
    heads = [q for pg in sorted(pages.values(), key=lambda x: x["id"]) for q in pg["head_queries"]]
    _, plan = fetch_serp(instance, heads, offline=offline, max_quota=max_quota)
    for pg in pages.values():
        v = value.value(pg["url"])
        pg["value"] = {"step": v["step"], "money": v["money"].to_dict(),
                       "leads": v["leads"].to_dict() if v["leads"] else None}
    candidates = []
    for c, mc in zip(clusters, map_clusters):
        if mc["status"] != FREE:
            continue
        key = Key.from_list(c["head_key"])
        candidates.append({"cluster_id": c["id"], "head": c["head"], "queries": [q["query"] for q in c["queries"]],
                           "weight": c["weight"], "direction": direction_of(instance, key)})
    total = sum(mc["weight"] for mc in map_clusters) or 0
    covered = sum(mc["weight"] for mc in map_clusters if mc["status"] in (COVERED, COMMERCIAL))
    with_money = sum(mc["weight"] for mc in map_clusters if mc["owner"]
                     and pages[mc["owner"]]["value"]["step"] in (1, 2)
                     and (pages[mc["owner"]]["value"]["money"]["value"] or 0) > 0)
    sections = p["site"]["article_sections"]
    rec = reconcile(pages_corpus, sitemap, sections) if sitemap else None
    report = {
        "date": instance.today.isoformat(), "pages": {k: sum(1 for x in pages.values() if x["type"] == k)
                                                      for k in (ARTICLE, SALES, OTHER)},
        "corpus_pages": len(pages_corpus), "sitemap": rec, "sitemap_error": sitemap_error,
        "clusters": {s: sum(1 for m in map_clusters if m["status"] == s)
                     for s in (FREE, RESERVED, COVERED, COMMERCIAL, FROZEN)},
        "covered_share": round(covered / total, 4) if total else None,
        "money_share": round(with_money / total, 4) if total else None,
        "live_overlaps": live_overlaps, "without_sections": corpus_report["without_sections"],
        "audit_overlaps": _audit_overlaps(instance),
        "candidates": len(candidates), "serp_heads": plan.line(),
    }
    line = store.write_json(instance.file("demand_map"), "demand_map", list(pages.values()),
                            meta={"clusters": map_clusters, "candidates": candidates, "report": report},
                            source_count=len(pages))
    report["write"] = line
    return report


def _audit_overlaps(instance) -> dict:
    """Д3 → А3: пересечения живых страниц из аудита недели (`data/overlaps.json`) — только чтение: карта их
    показывает, но реестр не перестраивает и не стирает."""
    records = store.read_json(instance.file("overlaps"), "overlaps", missing_ok=True)["records"]
    return {"pairs": len(records), "undecided": sum(1 for r in records if not r.get("decision")),
            "sales": sum(1 for r in records if r.get("kind") == "sales")}


def load(instance) -> dict:
    return store.read_json(instance.file("demand_map"), "demand_map")


def render(report: dict) -> str:
    lines = [f"Карта спроса — {report['date']}",
             f"Страниц: статей {report['pages'][ARTICLE]}, продающих {report['pages'][SALES]}, прочих "
             f"{report['pages'][OTHER]}; в корпусе {report['corpus_pages']}"]
    rec = report["sitemap"]
    if rec:
        lines += [f"Сверка с картой сайта (разделы статей, по slug): в карте сайта {rec['sitemap']}, в корпусе "
                  f"{rec['corpus']}, совпало {rec['both']}",
                  f"  только в корпусе ({len(rec['only_corpus'])}): " + ", ".join(rec["only_corpus"]),
                  f"  только в карте сайта ({len(rec['only_sitemap'])}): " + ", ".join(rec["only_sitemap"]),
                  f"  переехали между разделами (не расхождение): {len(rec['moved_section'])}"]
    else:
        lines.append(f"Сверка с картой сайта: не сделана — {report['sitemap_error'] or 'карта сайта не задана'}")
    ao = report.get("audit_overlaps") or {}
    if ao.get("pairs"):
        lines.append(f"Пересечения живых из аудита: пар {ao['pairs']}, без решения {ao['undecided']}, статья↔продающая "
                     f"{ao['sales']} — `audit accept`")
    lines.append("Кластеры по статусам: " + ", ".join(f"{k} — {v}" for k, v in report["clusters"].items()))
    share = report["covered_share"]
    money = report["money_share"]
    lines.append(f"Доля закрытого спроса (по весу кластеров): {share if share is not None else 'нет данных'}; "
                 f"закрыто страницами с деньгами: {money if money is not None else 'нет данных'}")
    lines.append(f"Пересечений живых: {len(report['live_overlaps'])}")
    for item in report["live_overlaps"][:50]:
        lines.append(f"  «{item['head']}» ({item['cluster_id']}): {', '.join(item['pages'])}")
    lines.append(f"Статьи без разделов ({len(report['without_sections'])}): " + ", ".join(report["without_sections"]))
    lines.append(f"Кандидатов (free): {report['candidates']}")
    lines.append(report["serp_heads"])
    lines.append(report.get("write", ""))
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("map", help="карта спроса: build")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("build", help="собрать карту спроса")
    add_profile(p)
    p.add_argument("--offline", action="store_true", help="без запросов к выдаче головных запросов")
    p.add_argument("--max-quota", type=int, help="объектов квоты на прогон, не больше")
    from ..core.budget import add_flags
    add_flags(p)                                 # выдача при отказе Keys.so — запасной платный источник
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    report = build_map(instance, offline=args.offline, max_quota=args.max_quota)
    text = render(report)
    folder = instance.reports_dir()
    (folder / "Карта спроса.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    (folder / "Карта спроса.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8",
                                              newline="\n")
    print(text)
    return 0
