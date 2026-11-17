"""Выгрузка корпуса по карте сайта (1.6.0, аудит 17.11.2026, Н17): `content-engine corpus import`.

Машина читает живые страницы только из папок markdown с шапкой (`adapters/corpus.py`). Сайту с историей на CMS
нужен экспорт всех текстов — команда делает его сама, без своего скрипта проекта: адреса — из `site.sitemap_file`
или `site.sitemap_url` (индекс карт обходится: дочерние карты только хостов `site.domains`, не больше
`MAX_SITEMAPS`), отбор — разделы `site.article_sections` (с `--sales` — ещё продающие разделы и адреса). Каждая
страница — один GET своего сайта (`PageFetcher.for_own_site`: белый список хостов, без перенаправлений, предел
размера); не 200 — «пропущено». Текст — основной блок (`prepare.main_blocks`, иначе `body` без меню и подвала):
подзаголовки h2–h4 — заголовками markdown, текст блоков (и `div`) — абзацами. Дата — `article:published_time`,
`<time datetime>` основного блока, иначе `lastmod` карты.

Файл — `<--into>/<slug>.md` с шапкой `slug`, `title`, `url`, `date`, `source: corpus import <день>`; повторный прогон
перезаписывает только свои файлы (шапка `source` начинается с «corpus import»), чужие в папке не трогает. После
записи — чтение обратно по записанным slug: «записано N — прочитано N». Команда печатает строку `corpus.folders` для
профиля: папку в профиль вписывает человек.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..adapters.corpus import LOC, iso_day, split_front_matter
from ..core.errors import EngineError, SourceError
from ..core.htmldoc import BLOCK, CHROME, SKIP_ALWAYS, HtmlDoc, Node
from ..core.urls import host_of, host_unicode, norm_url, section_of, slug_of

SOURCE = "corpus import"
MAX_SITEMAPS = 50
HEADINGS = {"h2": 2, "h3": 3, "h4": 4}
URL_BLOCK = re.compile(r"<url>(.*?)</url>", re.S | re.I)
LASTMOD = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", re.I)
SITEMAP_BLOCK = re.compile(r"<sitemap>(.*?)</sitemap>", re.S | re.I)


def _full(url: str) -> str:
    return url if url.startswith("http") else "https://" + url.lstrip("/")


def _entries(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(адреса страниц с lastmod, адреса дочерних карт) из текста карты сайта."""
    if "<sitemapindex" in text.lower():
        return [], [m for block in SITEMAP_BLOCK.findall(text) for m in LOC.findall(block)]
    blocks = URL_BLOCK.findall(text)
    if blocks:
        out = []
        for block in blocks:
            loc = LOC.findall(block)
            if loc:
                lm = LASTMOD.findall(block)
                out.append((loc[0], lm[0] if lm else ""))
        return out, []
    return [(line.strip(), "") for line in text.splitlines() if line.strip().startswith("http")], []


def sitemap_pages(instance, fetcher) -> tuple[dict[str, str], list[str]]:
    """Адрес (norm_url) → lastmod; заметки об обходе. Индекс карт обходится только по своим хостам."""
    site = instance.p["site"]
    own = {host_unicode(h) for h in site["domains"]}
    notes: list[str] = []
    if site["sitemap_file"]:
        path = Path(site["sitemap_file"])
        path = path if path.is_absolute() else instance.root / path
        texts = [path.read_text(encoding="utf-8-sig")]
    elif site["sitemap_url"]:
        got = fetcher.get(_full(site["sitemap_url"]))
        if got.status != 200:
            raise SourceError(f"карта сайта: HTTP {got.status}")
        texts = [got.text]
    else:
        raise EngineError("карты сайта нет: site.sitemap_url или site.sitemap_file")
    pages: dict[str, str] = {}
    seen: set[str] = set()
    while texts:
        entries, children = _entries(texts.pop(0))
        for loc, lastmod in entries:
            pages.setdefault(norm_url(loc), lastmod)
        for child in children:
            if host_of(child) not in own:
                notes.append(f"карта чужого хоста не запрошена: {child}")
                continue
            if norm_url(child) in seen:
                continue                               # повтор или цикл индекса — карта уже прочитана
            if len(seen) >= MAX_SITEMAPS:
                notes.append(f"дочерних карт больше {MAX_SITEMAPS} — остальные не прочитаны, корпус неполный")
                break
            seen.add(norm_url(child))
            got = fetcher.get(_full(child))
            if got.status != 200:
                notes.append(f"дочерняя карта {child}: HTTP {got.status}")
                continue
            texts.append(got.text)
    return pages, notes


def wanted(instance, urls: list[str], sales: bool) -> list[str]:
    site = instance.p["site"]
    sections = set(site["article_sections"]) | (set(site["sales_sections"]) if sales else set())
    sales_urls = {norm_url(u) for u in site["sales_urls"]} if sales else set()
    own = {host_unicode(h) for h in site["domains"]}
    out = []
    for u in urls:
        slug = slug_of(u)
        if host_of(u) not in own or not slug:
            continue
        if (section_of(u) in sections and slug not in sections) or u in sales_urls:
            out.append(u)
    return out


def _inline(node, skip) -> str:
    return node if isinstance(node, str) else node.text(skip=skip)


def to_markdown(main: Node, skip: set) -> list[str]:
    """Основной блок → строки markdown: h2–h4 — заголовки, текст блоков (и `div`) — абзацы, пункты списков — «- »."""
    out: list[str] = []

    def para(text: str, prefix: str = ""):
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(prefix + text)

    def walk(node: Node):
        if node.tag in skip or node.tag == "h1":
            return
        if node.tag in HEADINGS:
            para(node.text(skip=skip), "#" * HEADINGS[node.tag] + " ")
            return
        prefix = "- " if node.tag == "li" else ""
        blocks = [c for c in node.children if isinstance(c, Node) and (c.tag in BLOCK or c.tag in HEADINGS)]
        if not blocks:
            para(node.text(skip=skip), prefix)
            return
        run: list[str] = []
        for child in node.children:
            if isinstance(child, Node) and (child.tag in BLOCK or child.tag in HEADINGS):
                para(" ".join(run), prefix)
                run = []
                walk(child)
            elif not (isinstance(child, Node) and child.tag in skip):
                run.append(_inline(child, skip))
        para(" ".join(run), prefix)

    walk(main)
    return out


def _date(doc: HtmlDoc, main: Node, lastmod: str) -> str:
    for value in (doc._meta("article:published_time"),
                  next((n.attrs.get("datetime", "") for n in main.find_all("time") if n.attrs.get("datetime")), ""),
                  lastmod):
        day = iso_day(value) if value else ""
        if day:
            return day
    return ""


def page_file(instance, url: str, html: str, lastmod: str, today: str) -> tuple[dict, str]:
    doc = HtmlDoc(html, main_blocks=instance.p["prepare"]["main_blocks"])
    main, how = doc.main()
    skip = SKIP_ALWAYS | (CHROME if how == "body" else set())
    h1 = doc.headings(1)
    head = {"slug": slug_of(url), "title": (h1[0] if h1 else doc.title).strip(), "url": "https://" + url,
            "date": _date(doc, main, lastmod), "source": f"{SOURCE} {today}"}
    body = "\n\n".join(to_markdown(main, skip))
    return head, "---\n" + yaml.safe_dump(head, allow_unicode=True, sort_keys=False) + "---\n\n" + body + "\n"


def run_import(instance, into: str, limit: int | None, sales: bool, transport=None) -> dict:
    from ..adapters.page_fetch import PageFetcher
    fetcher = PageFetcher.for_own_site(instance, transport=transport)
    pages, notes = sitemap_pages(instance, fetcher)
    urls = wanted(instance, list(pages), sales)[: limit or None]
    folder = Path(into)
    folder = folder if folder.is_absolute() else instance.root / folder
    folder.mkdir(parents=True, exist_ok=True)
    today = instance.today.isoformat()
    written: dict[str, tuple[str, str]] = {}           # slug → (адрес, записанный текст)
    skipped: list[str] = []
    for url in urls:
        if slug_of(url) in written:
            skipped.append(f"{url}: тот же slug, что у {written[slug_of(url)][0]} — файл один, страница не записана")
            continue
        path = folder / f"{slug_of(url)}.md"
        if path.is_file():
            meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
            if not str(meta.get("source", "")).startswith(SOURCE):
                skipped.append(f"{url}: в папке чужой файл {path.name} — не перезаписан")
                continue
        got = fetcher.get(_full(url))
        if got.status != 200:
            skipped.append(f"{url}: HTTP {got.status}" + (f" → {got.location}" if got.location else ""))
            continue
        head, text = page_file(instance, url, got.text, pages[url], today)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        tmp.replace(path)                              # перезапись целиком: обрыв не оставляет полфайла
        written[head["slug"]] = (url, text)
    read = []
    for slug, (url, text) in written.items():
        path = folder / f"{slug}.md"
        meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
        if path.read_text(encoding="utf-8") == text and meta.get("slug") == slug and norm_url(meta.get("url", "")) == url:
            read.append(slug)
    try:
        rel = folder.relative_to(instance.root).as_posix()
    except ValueError:
        rel = folder.as_posix()
    spec = {"path": rel, "pattern": "*.md", "exclude": [],
            "fields": {"id": "slug", "slug": "slug", "title": "title", "url": "url", "date": "date"},
            "url_template": "", "section": (instance.p["site"]["article_sections"] or [""])[0], "index_csv": ""}
    return {"urls": len(urls), "written": len(written), "read": len(read), "skipped": skipped, "notes": notes,
            "folder": str(folder), "profile_folder": spec}


def render(out: dict) -> str:
    lines = [f"Корпус по карте сайта: адресов {out['urls']}, записано {out['written']} — прочитано {out['read']}, "
             f"пропущено {len(out['skipped'])}; папка {out['folder']}"]
    lines += [f"  пропущено: {s}" for s in out["skipped"]] + [f"  ! {n}" for n in out["notes"]]
    lines += ["", "Строка для профиля (corpus.folders, вписывает человек):",
              yaml.safe_dump([out["profile_folder"]], allow_unicode=True, sort_keys=False).rstrip()]
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    p = sub.add_parser("corpus", help="корпус: import — выгрузка опубликованных страниц по карте сайта в markdown")
    kinds = p.add_subparsers(dest="action", required=True)
    q = kinds.add_parser("import", help="страницы разделов из карты сайта → папка markdown с шапкой")
    add_profile(q)
    q.add_argument("--into", default="corpus/site", help="папка от инстанса (по умолчанию corpus/site)")
    q.add_argument("--limit", type=int, help="не больше N страниц")
    q.add_argument("--sales", action="store_true", help="и продающие страницы (sales_sections, sales_urls)")
    q.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    out = run_import(instance, args.into, args.limit, args.sales, transport=instance.transport)
    print(render(out))
    if out["written"] != out["read"]:
        return 3
    return 1 if out["urls"] and not out["written"] else 0
