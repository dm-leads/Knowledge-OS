"""Сборщик `html` (Т20.2): чистый HTML по шаблону проекта (`assemble.template`) или пакета (`templates/article.html`).

Разметка — по `assemble.schema_format`: JSON-LD скриптом в `<head>` или микроразметкой атрибутами в `<article>`
(Article, автор, даты, FAQPage с вопросами и ответами). Оглавление — якоря на разделы H2. В пакет идут страница,
`schema.json`, текст без разметки `text.txt` и манифест.
"""
from __future__ import annotations

import html
import json
from importlib import resources
from pathlib import Path
from string import Template

from ..adapters.corpus import split_front_matter
from ..core.markdown_html import convert, plain
from ..core.urls import norm_url
from .base import Package, write_manifest

READ_ALSO = "читайте также"


def template_text(instance) -> str:
    own = instance.p["assemble"]["template"]
    if own:
        path = Path(own)
        path = path if path.is_absolute() else instance.root / path
        return path.read_text(encoding="utf-8")
    return resources.files("content_engine.templates").joinpath("article.html").read_text(encoding="utf-8")


def read_also_links(body: str) -> list[str]:
    """Ссылки блока «Читайте также» текста — их финальная чистка сверяет с брифом."""
    from ..steps.gate_article import _links, _sections
    out = []
    for _, title, text in _sections(body):
        if title.lower().startswith(READ_ALSO):
            out += _links(text)
    return out


def microdata_block(schema: dict) -> str:
    nodes = {n.get("@type"): n for n in schema.get("@graph", [])}
    art = nodes.get("Article", {})
    esc = lambda v: html.escape(str(v), quote=True)       # noqa: E731
    parts = [f'<meta itemprop="headline" content="{esc(art.get("headline", ""))}">',
             f'<meta itemprop="description" content="{esc(art.get("description", ""))}">',
             f'<meta itemprop="datePublished" content="{esc(art.get("datePublished", ""))}">',
             f'<meta itemprop="dateModified" content="{esc(art.get("dateModified", ""))}">',
             f'<link itemprop="mainEntityOfPage" href="{esc(art.get("mainEntityOfPage", ""))}">',
             '<span itemprop="author" itemscope itemtype="https://schema.org/Person">'
             f'<meta itemprop="name" content="{esc((art.get("author") or {}).get("name", ""))}"></span>']
    faq = nodes.get("FAQPage")
    if faq:
        questions = "".join(
            '<div itemprop="mainEntity" itemscope itemtype="https://schema.org/Question">'
            f'<meta itemprop="name" content="{esc(q["name"])}">'
            '<div itemprop="acceptedAnswer" itemscope itemtype="https://schema.org/Answer">'
            f'<meta itemprop="text" content="{esc(q["acceptedAnswer"]["text"])}"></div></div>'
            for q in faq.get("mainEntity", []))
        parts.append(f'<div itemprop="hasPart" itemscope itemtype="https://schema.org/FAQPage">{questions}</div>')
    return "\n".join(parts)


class HtmlAssembler:
    name = "html"

    def __init__(self, instance, out_dir: Path):
        self.instance = instance
        self.out_dir = Path(out_dir)

    def build(self, article_path: Path, schema: dict, images: list[dict], brief: dict) -> Package:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        meta, body = split_front_matter(Path(article_path).read_text(encoding="utf-8"))
        body_html, headings = convert(body)
        h1 = next((h["text"] for h in headings if h["level"] == 1), str(brief.get("h1") or ""))
        toc = "".join(f'<li><a href="#{h["id"]}">{html.escape(h["text"])}</a></li>' for h in headings
                      if h["level"] == 2)
        art = next((n for n in schema.get("@graph", []) if n.get("@type") == "Article"), {})
        canonical = art.get("mainEntityOfPage", "")
        day = art.get("datePublished", self.instance.today.isoformat())
        author = (art.get("author") or {}).get("name", "")
        micro = self.instance.p["assemble"]["schema_format"] == "microdata"
        page = Template(template_text(self.instance)).safe_substitute(
            title=html.escape(str(brief.get("title") or h1)), description=html.escape(str(brief.get("description") or "")),
            canonical=html.escape(canonical),
            schema_script="" if micro else
            '<script type="application/ld+json">' + json.dumps({k: v for k, v in schema.items() if k != "slug"},
                                                              ensure_ascii=False) + "</script>",
            article_attrs=' itemscope itemtype="https://schema.org/Article"' if micro else "",
            microdata=microdata_block(schema) if micro else "",
            toc=f'<nav class="toc"><ol>{toc}</ol></nav>' if toc else "",
            body=body_html, author=html.escape(author), date=day)
        (self.out_dir / "article.html").write_text(page, encoding="utf-8", newline="\n")
        (self.out_dir / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8",
                                                  newline="\n")
        (self.out_dir / "text.txt").write_text(plain(body) + "\n", encoding="utf-8", newline="\n")
        manifest = {"id": brief.get("id", ""), "built": day, "title": str(brief.get("title") or h1),
                    "description": str(brief.get("description") or ""), "slug": str(brief.get("slug") or ""),
                    "h1": h1, "canonical": canonical, "main": "article.html", "blocks": ["article.html"],
                    "read_also": [norm_url(x) if x.startswith("http") else x for x in read_also_links(body)],
                    "assembler": self.name, "schema_format": self.instance.p["assemble"]["schema_format"],
                    "images": images}
        return write_manifest(self.out_dir, manifest, ["article.html", "schema.json", "text.txt"])
