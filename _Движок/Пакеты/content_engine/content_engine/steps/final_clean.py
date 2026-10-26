"""Финальная чистка перед публикацией (Т21.5): автор и даты на месте, внутренние ссылки отвечают 200, «Читайте также»
ведёт только на статьи из карты перелинковки брифа. Возвращает список проблем; пусто — можно публиковать.
"""
from __future__ import annotations

import yaml

from ..core.htmldoc import HtmlDoc
from ..core.urls import is_own, norm_url, slug_of


def check(instance, tid: str, package, *, transport=None) -> list[str]:
    from ..adapters.page_fetch import PageFetcher
    brief = yaml.safe_load((instance.brief_dir(tid) / "brief.yaml").read_text(encoding="utf-8")) or {}
    m = package.manifest
    problems = []
    main = package.main.read_text(encoding="utf-8")
    schema_file = package.folder / "schema.json"
    schema_text = schema_file.read_text(encoding="utf-8") if schema_file.is_file() else ""
    author = str((brief.get("author") or {}).get("name", "") if isinstance(brief.get("author"), dict)
                 else brief.get("author") or "").strip()
    if not author:
        problems.append("в брифе нет автора")
    elif author not in main and author not in schema_text:
        problems.append(f"автора «{author}» нет ни в странице, ни в разметке")
    if m["built"] not in main and m["built"] not in schema_text:
        problems.append(f"даты сборки {m['built']} нет ни в странице, ни в разметке (datePublished)")
    domains = instance.p["site"]["domains"]
    doc = HtmlDoc(main) if package.main.suffix == ".html" else None
    links = doc.links() if doc else _md_links(main)
    own = []
    for link in links:
        if link.startswith("#"):
            continue
        url = f"https://{domains[0]}{link}" if link.startswith("/") else link
        if url.startswith("http") and is_own(url, domains):
            own.append(url)
    fetcher = PageFetcher.for_own_site(instance, transport=transport)
    for url in dict.fromkeys(own):
        try:
            status = fetcher.get(url, allow_redirects=False).status
        except Exception as exc:                    # ссылка не проверена — это проблема, а не «ок»
            status = f"{type(exc).__name__}"
        if status != 200:
            problems.append(f"внутренняя ссылка {url} — {status}")
    read_also = brief.get("read_also") or []
    allowed = {slug_of(x) or x for x in read_also} | {norm_url(x) for x in read_also}
    for link in m.get("read_also", []):
        if slug_of(link) not in allowed and norm_url(link) not in allowed:
            problems.append(f"«Читайте также» ведёт на {link} — её нет в карте перелинковки брифа (read_also)")
    return problems


def _md_links(text: str) -> list[str]:
    from .gate_article import _links
    return _links(text)
