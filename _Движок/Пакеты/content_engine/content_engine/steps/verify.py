"""«Доехало» (Г2, Т22): `content-engine publish verify <id> [--url <адрес>]` — по ответу сайта, а не по записи файла.

Проверки (Т22.1): код 200 без перенаправлений; canonical на свой адрес; нет запрета индексации (мета robots и
заголовок X-Robots-Tag); title, description, H1 и slug совпадают с эталоном; текст — доля восьмисловных цепочек эталона,
найденных на странице, не ниже `publish.text_match`; разметка на месте и сходится с текстом. Эталон — манифест пакета;
у страницы без манифеста — её запись в корпусе (title, slug, текст), и отчёт пишет «description и H1 не сверялись».
Без `--url` адрес ищется по slug в карте сайта. Не доехало — причина по каждому пункту, код 1.

Успех: день выкладки — в журнал выпуска в день действия (первое «доехало»), день сборки — отдельным полем; дата в
тексте или разметке не равна дню выкладки — замечание с заданием поправить. Страница без записи — день
«восстановлен: <источник>» (lastmod карты сайта, первый день с показами в Search Console) или «не установлен».
Эталонный снимок — `data/snapshots/live/<slug>.json`. Затем сразу записи дня выкладки (Т22.7).
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

from ..adapters.page_fetch import PageFetcher
from ..core import publications, snapshots
from ..core.errors import EngineError, SourceError
from ..core.htmldoc import HtmlDoc
from ..core.urls import ascii_url, norm_url, section_of, slug_of
from .gate_article import _shingles
from .schema import problems as schema_problems

ARRIVED, NOT_ARRIVED = "доехало", "не доехало"


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _same_title(page: str, ref: str) -> bool:
    """Совпадает, или сайт дописал своё имя через разделитель: «Заголовок — Сайт»."""
    page, ref = _norm_text(page), _norm_text(ref)
    return bool(ref) and (page == ref or any(page.startswith(ref + sep) for sep in (" — ", " | ", " - ", " · ")))


def reference(instance, tid: str, url: str | None) -> dict:
    folder = instance.root / "data" / "briefs" / tid / "package"
    if (folder / "manifest.json").is_file():
        from ..assemblers.base import Package
        m = Package.load(folder).manifest
        return {"kind": "манифест", "id": tid, "title": m["title"], "description": m["description"], "h1": m["h1"],
                "slug": m["slug"], "canonical": m["canonical"], "text": Package.load(folder).text(),
                "built": m["built"]}
    pages, _ = instance.corpus()
    wanted = {tid, slug_of(url)} if url else {tid}
    page = next((p for p in pages if p.slug in wanted), None)
    if page is None:
        raise EngineError(f"эталона для «{tid}» нет: ни пакета data/briefs/{tid}/package, ни статьи корпуса "
                          "с таким slug")
    return {"kind": "корпус", "id": tid, "title": page.title, "description": None, "h1": None, "slug": page.slug,
            "canonical": "", "text": page.text, "built": None}


def find_url(instance, slug: str) -> str:
    from ..adapters.corpus import sitemap_urls
    urls = sitemap_urls(instance.p, instance.root, transport=instance.transport)
    sections = instance.p["site"]["article_sections"]
    hits = [u for u in urls if slug_of(u) == slug and (not sections or section_of(u) in sections)]
    if not hits:
        raise EngineError(f"адреса со slug «{slug}» в карте сайта нет — передайте --url")
    return "https://" + hits[0]


def check_page(instance, ref: dict, url: str, *, transport=None) -> dict:
    fetcher = PageFetcher.for_own_site(instance, transport=transport if transport is not None else instance.transport)
    items = []

    def add(name, ok, detail):
        items.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        got = fetcher.get(url, allow_redirects=False)
    except SourceError as exc:
        add("код ответа", False, str(exc))
        return {"items": items, "doc": None}
    redirect = f" → {got.location}" if got.location else ""
    add("код ответа", got.status == 200, f"HTTP {got.status}{redirect}" + (" (перенаправление)" if redirect else ""))
    if got.status != 200:
        return {"items": items, "doc": None}
    doc = HtmlDoc(got.text, instance.p["prepare"]["main_blocks"])
    add("canonical", doc.canonical and norm_url(doc.canonical) == norm_url(url),
        f"canonical «{doc.canonical or 'нет'}»" + ("" if doc.canonical and norm_url(doc.canonical) == norm_url(url)
                                                   else f" — не свой адрес {norm_url(url)}"))
    robots = f"{doc.robots} {got.robots_header}".lower()
    add("индексация", "noindex" not in robots, f"robots «{doc.robots or '—'}», X-Robots-Tag «{got.robots_header or '—'}»")
    add("title", _same_title(doc.title, ref["title"]), f"на странице «{doc.title}», эталон «{ref['title']}»")
    if ref["description"] is not None:
        add("description", _norm_text(doc.description) == _norm_text(ref["description"]),
            f"на странице «{doc.description}», эталон «{ref['description']}»")
    h1s = doc.headings(1)
    if ref["h1"] is not None:
        add("H1", _norm_text(ref["h1"]) in {_norm_text(h) for h in h1s}, f"на странице {h1s}, эталон «{ref['h1']}»")
    add("slug", slug_of(url) == ref["slug"], f"адрес «{slug_of(url)}», эталон «{ref['slug']}»")
    page_text = doc.root.text()
    theirs, mine = _shingles(page_text), _shingles(ref["text"])
    share = len(mine & theirs) / len(mine) if mine else 0.0
    need = instance.p["publish"]["text_match"]
    add("текст", mine and share >= need, f"доля восьмисловных цепочек эталона на странице {share:.0%} "
                                         f"(порог {need:.0%})" if mine else "эталонного текста нет")
    nodes = doc.schema_nodes()
    broken = [str(b) for b in doc.json_ld() if isinstance(b, Exception)]
    found = schema_problems(nodes, schema_slug=slug_of(doc.canonical) if doc.canonical else "",
                            title=ref["title"], h1s=[ref["h1"]] if ref["h1"] else h1s, slug=ref["slug"],
                            faq_text=page_text) if nodes else ["разметки нет"]
    add("разметка", not broken and not found, "; ".join(broken + found) or
        f"узлов {len(nodes)}: {', '.join(sorted({str(n.get('@type')) for n in nodes}))}")
    return {"items": items, "doc": doc}


def restore_day(instance, url: str) -> tuple[str | None, str]:
    """Т22.5: день выкладки страницы без записи — из lastmod карты сайта, затем из Search Console."""
    text = ""
    site = instance.p["site"]
    try:
        if site["sitemap_file"]:
            path = Path(site["sitemap_file"])
            text = (path if path.is_absolute() else instance.root / path).read_text(encoding="utf-8-sig")
        elif site["sitemap_url"]:
            text = PageFetcher.for_own_site(instance).get(site["sitemap_url"]).text
    except (OSError, SourceError):
        text = ""
    for block in re.findall(r"<url>(.*?)</url>", text, re.S | re.I):
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block, re.I)
        mod = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", block, re.I)
        if loc and mod and norm_url(loc.group(1)) == norm_url(url):
            return mod.group(1)[:10], "восстановлен: lastmod карты сайта"
    g = instance.p["sources"]["gsc"]
    if g["enabled"]:
        try:
            from ..adapters.gsc import SearchConsole, service_account_token
            console = SearchConsole(g["site_url"], service_account_token(g["key_file"]), instance.today.isoformat(),
                                    transport=instance.transport)
            first = console.first_day(ascii_url(url), (instance.today - timedelta(days=486)).isoformat(),
                                      instance.today.isoformat())
            if first:
                return first, "восстановлен: первый день с показами в Search Console"
        except (SourceError, OSError, ValueError) as exc:
            return None, f"не установлен (Search Console: {exc})"
    return None, "не установлен"


def verify(instance, tid: str, url: str | None, *, browser: bool = True, transport=None) -> dict:
    ref = reference(instance, tid, url)
    if not url:
        if instance.p["publish"]["mode"] != "manual":
            raise EngineError("без --url адрес ищется по карте сайта только в ручном режиме")
        url = find_url(instance, ref["slug"])
    url = url if url.startswith("http") else "https://" + url.lstrip("/")
    page = check_page(instance, ref, url, transport=transport)
    items, doc = page["items"], page["doc"]
    layout = {"status": "не проверялось", "why": "страница не ответила 200"}
    if doc is not None and browser:
        from . import layout as layout_mod
        layout = layout_mod.check(instance, url)
    elif doc is not None:
        layout = {"status": "не подключено", "why": "--no-browser"}
    arrived = all(i["ok"] for i in items) and layout["status"] != "не прошло"
    report = {"id": tid, "url": url, "date": instance.today.isoformat(), "reference": ref["kind"],
              "verdict": ARRIVED if arrived else NOT_ARRIVED, "items": items, "layout": layout, "notes": []}
    if ref["kind"] == "корпус":
        report["notes"].append("description и H1 не сверялись: у страницы нет манифеста, эталон — запись корпуса")
    if arrived:
        report.update(_day(instance, tid, ref, url, doc, report["notes"]))
        live = {"url": url, "date": instance.today.isoformat(), "status": 200, "canonical": doc.canonical,
                "robots": doc.robots, "title": doc.title, "description": doc.description, "h1": doc.headings(1),
                "schema": sorted({str(n.get("@type")) for n in doc.schema_nodes()}), "og_image": doc.og_image,
                "text_chars": len(re.sub(r"\s+", "", doc.main_text()))}
        report["live_snapshot"] = str(snapshots.save_live(instance, ref["slug"], live))
    return report


def _day(instance, tid, ref, url, doc, notes) -> dict:
    if ref["kind"] == "манифест":
        first = publications.published(instance, tid=tid)
        if first is None:
            from .records import release_fields
            publications.log(instance, "published", id=tid, url=norm_url(url), day=instance.today.isoformat(),
                             built=ref["built"], **release_fields(instance, tid, ref))
            first = publications.published(instance, tid=tid)
        day = first["day"]
        dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", doc.root.text() + json.dumps(doc.schema_nodes(), ensure_ascii=False)))
        stale = sorted(d for d in dates if d != day and d == ref["built"])
        if stale:
            notes.append(f"дата сборки {ref['built']} на странице или в разметке ≠ день выкладки {day}: поправить "
                         "дату в тексте и datePublished/dateModified и вставить заново")
        return {"publication_day": day, "built": first.get("built"), "day_source": "журнал выпуска"}
    first = publications.published(instance, url=norm_url(url))
    if first:
        return {"publication_day": first["day"], "built": None, "day_source": "журнал выпуска"}
    day, how = restore_day(instance, url)
    return {"publication_day": day, "built": None, "day_source": how}


def render(report: dict) -> str:
    lines = [f"«Доехало» {report['id']}: {report['url']} — {report['verdict'].upper()} (эталон: {report['reference']})"]
    for i in report["items"]:
        lines.append(f"  {'✓' if i['ok'] else '✗'} {i['check']}: {i['detail']}")
    lay = report["layout"]
    lines.append(f"  вёрстка: {lay['status']}" + (f" — {lay['why']}" if lay.get("why") else ""))
    for w in lay.get("widths", []):
        lines.append(f"    {w['width']}px: {'ок' if w['ok'] else 'нет'}, снимок {w['screenshot']}")
    if lay.get("network") is not None and lay["status"] in ("прошло", "не прошло"):
        lines.append(f"    счётчики заблокированы: {len(lay['network'])} запросов, дошло до счётчиков: "
                     f"{lay.get('counters_reached', 0)}")
    if report.get("publication_day") or report.get("day_source"):
        lines.append(f"  день выкладки: {report.get('publication_day') or '—'} ({report['day_source']})"
                     + (f", день сборки {report['built']}" if report.get("built") else ""))
    lines += [f"  ! {n}" for n in report["notes"]]
    return "\n".join(lines)


def run_verify(instance, tid: str, url: str | None, *, browser: bool = True, apply: bool = False) -> int:
    report = verify(instance, tid, url, browser=browser)
    folder = instance.reports_dir()
    (folder / f"Доехало — {tid}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8",
                                                  newline="\n")
    print(render(report))
    if report["verdict"] != ARRIVED:
        return 1
    if report["reference"] == "манифест":
        from .records import run_records
        return run_records(instance, tid, apply=apply)
    return 0
