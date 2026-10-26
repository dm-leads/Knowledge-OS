"""Сборщик `markdown` (Т20.2): MD с шапкой для статических сайтов (Hugo, Jekyll, Astro и подобных).

Шапка — title, description, slug, date, author, canonical; разметка JSON-LD — отдельным `schema.json` рядом (шаблон
сайта вставляет её сам). Главный файл — `article.md`; публикатор `static_repo` кладёт его как `<slug>.md`.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from ..adapters.corpus import split_front_matter
from ..core.markdown_html import plain
from ..core.urls import norm_url
from .base import Package, write_manifest
from .html import read_also_links


class MarkdownAssembler:
    name = "markdown"

    def __init__(self, instance, out_dir: Path):
        self.instance = instance
        self.out_dir = Path(out_dir)

    def build(self, article_path: Path, schema: dict, images: list[dict], brief: dict) -> Package:
        from ..steps.schema import h1_of
        self.out_dir.mkdir(parents=True, exist_ok=True)
        _, body = split_front_matter(Path(article_path).read_text(encoding="utf-8"))
        art = next((n for n in schema.get("@graph", []) if n.get("@type") == "Article"), {})
        head = {"title": str(brief.get("title") or ""), "description": str(brief.get("description") or ""),
                "slug": str(brief.get("slug") or ""), "date": art.get("datePublished", ""),
                "author": (art.get("author") or {}).get("name", ""), "canonical": art.get("mainEntityOfPage", "")}
        text = "---\n" + yaml.safe_dump(head, allow_unicode=True, sort_keys=False) + "---\n\n" + body.lstrip()
        (self.out_dir / "article.md").write_text(text, encoding="utf-8", newline="\n")
        (self.out_dir / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8",
                                                  newline="\n")
        (self.out_dir / "text.txt").write_text(plain(body) + "\n", encoding="utf-8", newline="\n")
        manifest = {"id": brief.get("id", ""), "built": head["date"], "title": head["title"],
                    "description": head["description"], "slug": head["slug"], "h1": h1_of(body),
                    "canonical": head["canonical"], "main": "article.md", "blocks": ["article.md"],
                    "read_also": [norm_url(x) if x.startswith("http") else x for x in read_also_links(body)],
                    "assembler": self.name, "schema_format": "json-ld", "images": images}
        return write_manifest(self.out_dir, manifest, ["article.md", "schema.json", "text.txt"])
