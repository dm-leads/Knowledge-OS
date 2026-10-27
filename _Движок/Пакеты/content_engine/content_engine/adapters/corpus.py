"""Корпус опубликованного (Т4.3): тексты статей с разделами и список адресов из карты сайта.

`markdown_folder` — папки текстов с шапкой YAML по порядку из профиля; при одинаковом slug побеждает последняя
(свежий текст выпуска главнее старого снимка). Разделы — по markdown-подзаголовкам; если их нет — по оглавлению:
пункты блока после маркера (например «Содержание»), которые ниже встречаются отдельной строкой, — границы разделов;
нет ни того, ни другого — статья одним разделом, такие статьи идут в отчёт.
`sitemap` — адреса из файла или одного GET карты сайта; сверка с корпусом — по slug без учёта раздела: статьи
переезжают между разделами, адрес в шапке может быть старым.
"""
from __future__ import annotations

import csv
import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..core.errors import EngineError, SourceError
from ..core.urls import norm_url, section_of, slug_of

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
ROLES = ("id", "slug", "title", "url", "date")   # date — день публикации из шапки (книга ставок задним числом)


@dataclass
class Section:
    heading: str
    text: str


@dataclass
class Page:
    slug: str
    url: str
    title: str
    kind: str = "article"              # article | sales
    id: str = ""
    sections: list[Section] = field(default_factory=list)
    split: str = "none"                # headings | toc | none — как найдены разделы
    path: str = ""                     # файл текста
    date: str = ""                     # день публикации из шапки (роль date), ГГГГ-ММ-ДД; пусто — не указан

    @property
    def section(self) -> str:
        return section_of(self.url)

    @property
    def headings(self) -> list[str]:
        return [s.heading for s in self.sections if s.heading]

    @property
    def text(self) -> str:
        return "\n".join(f"{s.heading}\n{s.text}" for s in self.sections)


MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8, "сен": 9,
          "окт": 10, "ноя": 11, "дек": 12}


def iso_day(value) -> str:
    """День публикации из шапки: ГГГГ-ММ-ДД (в том числе с временем), ДД.ММ.ГГГГ или «02 марта 2022»; иначе пусто."""
    text = str(value or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.match(r"^(\d{1,2})\s+([а-яё]+)\s+(\d{4})", text.lower())   # «02 марта 2022» — как пишет сайт
    month = MONTHS.get(m.group(2)[:3]) if m else None
    return f"{m.group(3)}-{month:02d}-{int(m.group(1)):02d}" if month else ""


def _clean(line: str) -> str:
    line = line.strip().lower().replace("ё", "е")
    line = re.sub(r"^[#*\-\d.\s)]+", "", line)
    line = re.sub(r"[\s:.!?*]+$", "", line)
    return re.sub(r"\s+", " ", line)


def split_front_matter(text: str) -> tuple[dict, str]:
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return {}, text
    end = re.search(r"^---\s*$", text[3:], re.MULTILINE)
    if not end:
        return {}, text
    head, body = text[3:3 + end.start()], text[3 + end.end():]
    try:
        meta = yaml.safe_load(head) or {}
        if not isinstance(meta, dict):
            meta = {}
    except yaml.YAMLError:
        meta = {}
        for line in head.splitlines():
            name, sep, value = line.partition(":")
            if sep and not line.startswith((" ", "\t")):
                meta[name.strip()] = value.strip().strip("'\"")
    return {str(k): v for k, v in meta.items()}, body


def split_sections(body: str, toc_marker: str) -> tuple[list[Section], str]:
    """Разделы статьи и способ, которым они найдены."""
    lines = body.splitlines()
    marks = [(i, len(m.group(1)), m.group(2)) for i, line in enumerate(lines) if (m := HEADING.match(line))]
    subheads = [(i, text) for i, level, text in marks if level >= 2]
    if subheads:
        h1 = {i for i, level, _ in marks if level == 1}
        sections = [Section("", "\n".join(l for k, l in enumerate(lines[:subheads[0][0]]) if k not in h1))]
        for n, (i, heading) in enumerate(subheads):
            stop = subheads[n + 1][0] if n + 1 < len(subheads) else len(lines)
            sections.append(Section(heading.strip(), "\n".join(lines[i + 1:stop])))
        return [s for s in sections if s.heading or s.text.strip()], "headings"
    toc = _toc_sections(lines, toc_marker) if toc_marker else None
    if toc:
        return toc, "toc"
    return [Section("", "\n".join(l for l in lines if not HEADING.match(l)))], "none"


def _toc_sections(lines: list[str], marker: str) -> list[Section] | None:
    target = _clean(marker)
    start = next((i for i, line in enumerate(lines) if _clean(line) == target), None)
    if start is None:
        return None
    j = start + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    items = []
    while j < len(lines) and lines[j].strip():
        items.append(lines[j].strip())
        j += 1
    bounds, k = [], j
    for item in items:
        wanted = _clean(item)
        for m in range(k, len(lines)):
            if _clean(lines[m]) == wanted:
                bounds.append((m, item))
                k = m + 1
                break
    if not bounds:
        return None
    sections = [Section("", "\n".join(lines[:start] + lines[j:bounds[0][0]]))]
    for n, (m, heading) in enumerate(bounds):
        stop = bounds[n + 1][0] if n + 1 < len(bounds) else len(lines)
        sections.append(Section(heading, "\n".join(lines[m + 1:stop])))
    return sections


def _field(source: str, meta: dict, row: dict, path: Path) -> str:
    """Значение поля: из шапки, указателя или соседнего файла.

    «имя» — поле шапки или колонка указателя; «@файл.json:ключ» — ключ JSON-файла рядом с текстом;
    «@шаблон» — число из имени последнего подходящего файла рядом с текстом (например, номер строки базы статей).
    """
    if not source.startswith("@"):
        return str(meta.get(source) or row.get(source) or "").strip()
    spec = source[1:]
    if ":" in spec:
        name, key = spec.split(":", 1)
        sidecar = path.parent / name
        if not sidecar.is_file():
            return ""
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return ""
        return str(data.get(key) or "").strip() if isinstance(data, dict) else ""
    found = sorted(path.parent.glob(spec))
    digits = re.findall(r"\d+", found[-1].stem) if found else []
    return digits[-1] if digits else ""


def _excluded(path: Path, root: Path, exclude: list[str]) -> bool:
    parts = path.relative_to(root).parts
    return any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in exclude)


def _index(folder: Path, name: str, fields: dict) -> dict[str, dict]:
    if not name:
        return {}
    path = folder / name
    if not path.is_file():
        raise SourceError(f"указатель корпуса не найден: {path}")
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))
    id_field = next((k for k, role in fields.items() if role == "id"), "id")
    return {str(r.get(id_field, "")).strip(): r for r in rows}


def markdown_folder(profile: dict, root: Path) -> tuple[list[Page], dict]:
    """Страницы корпуса и отчёт: сколько файлов прочитано в каждой папке, у кого нет slug, кто без разделов."""
    domain = profile["site"]["domains"][0]
    marker = profile["corpus"]["toc_marker"]
    by_slug: dict[str, Page] = {}
    report = {"folders": [], "no_slug": [], "replaced": 0}
    for spec in profile["corpus"]["folders"]:
        folder = Path(spec["path"])
        folder = folder if folder.is_absolute() else root / folder
        if not folder.is_dir():
            raise SourceError(f"папка корпуса не найдена: {folder}")
        unknown = set(spec["fields"].values()) - set(ROLES)
        if unknown:
            raise EngineError(f"corpus.folders.fields: неизвестные роли {sorted(unknown)}; роли — {ROLES}")
        index = _index(folder, spec["index_csv"], spec["fields"])
        read = 0
        for path in sorted(folder.glob(spec["pattern"])):
            if not path.is_file() or _excluded(path, folder, spec["exclude"]):
                continue
            meta, body = split_front_matter(path.read_text(encoding="utf-8"))
            id_source = next((s for s, role in spec["fields"].items() if role == "id" and not s.startswith("@")), "id")
            row = index.get(str(meta.get(id_source, "")).strip(), {})
            value = {}
            for source, role in spec["fields"].items():      # у роли может быть несколько источников: первый непустой
                if not value.get(role):
                    value[role] = _field(source, meta, row, path)
            url = norm_url(value.get("url", "")) if value.get("url") else ""
            slug = value.get("slug") or slug_of(url)
            if not slug:
                report["no_slug"].append(str(path.relative_to(folder)))
                continue
            if not url:
                if not spec["url_template"]:
                    raise EngineError(f"corpus: у папки {folder.name} нет поля url и шаблона адреса")
                url = norm_url(spec["url_template"].format(domain=domain, section=spec["section"], slug=slug))
            sections, how = split_sections(body, marker)
            h1 = next((m.group(2) for line in body.splitlines() if (m := HEADING.match(line)) and m.group(1) == "#"), "")
            title = value.get("title") or h1
            if slug in by_slug:
                report["replaced"] += 1
            by_slug[slug] = Page(slug=slug, url=url, title=title, id=value.get("id", ""), sections=sections,
                                 split=how, path=str(path), date=iso_day(value.get("date", "")))
            read += 1
        report["folders"].append({"path": str(folder), "read": read})
    pages = list(by_slug.values())
    report["pages"] = len(pages)
    report["without_sections"] = sorted(p.slug for p in pages if p.split == "none")
    return pages, report


def sitemap_urls(profile: dict, root: Path, transport=None) -> list[str]:
    """Адреса карты сайта: файл (XML или список построчно) или один GET адреса из профиля."""
    site = profile["site"]
    if site["sitemap_file"]:
        path = Path(site["sitemap_file"])
        path = path if path.is_absolute() else root / path
        text = path.read_text(encoding="utf-8-sig")
    elif site["sitemap_url"]:
        from .base import ReadOnlyClient
        url = site["sitemap_url"]
        base, _, endpoint = url.rpartition("/")
        client = ReadOnlyClient("карта сайта", base, {endpoint}, {"User-Agent": "content-engine"},
                                transport=transport, retries=2)
        text = _get_text(client, endpoint)
    else:
        return []
    found = LOC.findall(text)
    if "<sitemapindex" in text.lower():
        raise SourceError("карта сайта — индекс карт: укажите карту раздела или файл (sitemap_file)")
    if not found:
        found = [line.strip() for line in text.splitlines() if line.strip().startswith("http")]
    return [norm_url(u) for u in found]


def _get_text(client, endpoint: str) -> str:
    url = f"{client.base_url}/{endpoint}"
    if endpoint not in client.allowed:
        raise SourceError("адрес карты сайта вне белого списка")
    response = client.transport("GET", url, params=None, json=None, headers=client.headers, timeout=client.timeout)
    if response.status_code in (401, 403):
        from ..core.errors import AccessDenied
        raise AccessDenied(f"карта сайта: HTTP {response.status_code}")
    if response.status_code != 200:
        raise SourceError(f"карта сайта: HTTP {response.status_code}")
    response.encoding = response.encoding or "utf-8"
    return response.text


def article_slugs(urls: list[str], sections: list[str]) -> dict[str, str]:
    """slug → адрес для адресов из разделов статей."""
    return {slug_of(u): u for u in urls if section_of(u) in sections and slug_of(u)}


def reconcile(pages: list[Page], urls: list[str], sections: list[str]) -> dict:
    """Сверка корпуса с картой сайта по slug без учёта раздела (статьи переезжают, адрес в шапке бывает старым)."""
    site = article_slugs(urls, sections)
    corpus = {p.slug: p for p in pages}
    moved = sorted(s for s in site if s in corpus and section_of(site[s]) != corpus[s].section)
    return {"corpus": len(corpus), "sitemap": len(site), "both": len(set(site) & set(corpus)),
            "only_corpus": sorted(set(corpus) - set(site)), "only_sitemap": sorted(set(site) - set(corpus)),
            "moved_section": moved}
