"""Проверка методобазы Knowledge-OS как одной системы (аудит связки 17.11.2026, раздел «Предложение: постоянный скрипт»).

Только чтение, кроме режима `write` (перезаписывает одну колонку карты) и `baseline` (пишет базу нарушений). Что
проверяется (правило — уровень):
- ссылки во всех `.md` под `Методологии\\` и в `SKILL.md` навыков: файла нет (`link-broken`), вики-ссылка
  (`link-wiki`), абсолютный путь в документе (`link-absolute`; в `SKILL.md` абсолютные пути — норма, проверяется только
  существование целей внутри репозитория) — ошибки. Inline-код и блоки кода ссылками не считаются;
- `[документ](…), раздел N` / `разделы 3 и 7` / `шаг N` / `раздел «Название»` — раздела нет среди заголовков,
  нумерованных строк таблиц и пунктов списков цели (`section-missing`) — ошибка;
- ссылка из ядра (`Методологии\\` без `_исходные-заметки\\`) за пределы корней канона `_Движок`, `Методологии`,
  `Экосистема инструментов` (`link-outside-canon`) — ошибка;
- frontmatter ядра: есть и разбирается строгим YAML, обязательные поля и даты, `status` и `review_cycle` из закрытых
  списков, `type` из закрытого списка, `type` ↔ префикс имени по словарю `PREFIX_TYPES` — ошибки; неизвестный
  префикс — предупреждение. В `_исходные-заметки\\` frontmatter и префиксы не проверяются;
- документ не назван в манифесте своей папки (`manifest-catalog`); хозяин не назван в карте методобазы ни прямо, ни
  через манифест папки (`map-owner`); колонка «Зрелость» карты расходится со статусом (`map-maturity`) — ошибки;
- у хозяина нет блока «Место в системе» (`passport-block`) — ошибка: старые хозяева без блока лежат в базе, новый
  не пройдёт;
- навыки: назван в карте или `CLAUDE.md`, но его нет (`skill-unknown`); есть в исходниках, но не назван в `CLAUDE.md`
  с учётом шаблонов `growth-*`, `ads-*` (`skill-not-in-claude`); описание длиннее 1024 знаков
  (`skill-description`) — ошибки; не стоит ссылкой в `~/.claude/skills` (`skill-not-linked`), frontmatter не строгий
  YAML (`skill-yaml`), A называет B в «Границах», B не называет A (`skill-neighbors`) — предупреждения;
- словарь хозяев `kos_methodbase_owners.json`: якорь и шаблон в одной строке вне хозяина без ссылки на хозяина
  (`owner-copy`) — предупреждение; хозяина или его раздела нет (`owners-config`) — ошибка;
- имена проектов и длинные идентификаторы в ядре вне строк происхождения (`project-name`), футер «Связанные
  документы» длиннее 10 или его нет у содержательного типа (`footer-long`, `footer-missing`) — предупреждения.

База нарушений `kos_methodbase_baseline.json`: у каждой ошибки устойчивый ключ «правило | путь | суть» без номера
строки; `check` падает только на ошибках сверх базы (счёт по ключу: нарушений не больше, чем было). Базу пишет
только `baseline` — по решению владельца.

Запуск (из этой папки):
  py -3 kos_methodbase_check.py check [--changed] [--json] [--verbose]
  py -3 kos_methodbase_check.py write [--json]     — перезаписать колонку «Зрелость» карты методобазы
  py -3 kos_methodbase_check.py baseline           — записать текущие ошибки в базу
Общие ключи: --root (корень Knowledge-OS), --owners, --baseline, --skills-home.
`check --changed` — документы из индекса git (`git diff --cached --name-status`), документы, ссылающиеся на
изменённые, переименованные и удалённые пути, и правила на пары документов по всей базе.
Код возврата: 0 — новых ошибок нет (предупреждения печатаются); 1 — есть новые ошибки; 2 — сбой самого скрипта
(нет корня, файл не читается как UTF-8, словарь или база не разбираются, ноль проверенных файлов, git не ответил).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote

import yaml

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = TOOL_DIR.parents[1]
DEFAULT_OWNERS = TOOL_DIR / "kos_methodbase_owners.json"
DEFAULT_BASELINE = TOOL_DIR / "kos_methodbase_baseline.json"
DEFAULT_SKILLS_HOME = Path.home() / ".claude" / "skills"

METHODS = "Методологии"
SKILLS_SRC = ("_Движок", "Исходники скиллов")
CANON_ROOTS = ("_Движок", "Методологии", "Экосистема инструментов")
RAW = "_исходные-заметки"
APPLIED = "Применение"
# Сторонние корпуса внутри методобазы: свой формат, не документы канона — только проверка ссылок.
FOREIGN = ("Next-Move-Theory-Canon", "Ivan-Zamesin", "_Красинский — полный разбор", "Каталог операций",
           "0 — System Docs", "Шаблоны")
MAP_FILE = "Справочник — Карта методобазы- задача, документ, навык.md"
MANIFEST = "_manifest.md"

# Закрытый список типов: конвенция типов + решение владельца 17.11.2026 (plan, prompt; ADR готовится).
TYPES = {
    "policy", "standard", "workflow", "runbook", "playbook", "instruction", "methodology", "reference", "guide",
    "bootstrap", "template", "analytics", "retrospective", "postmortem", "audit", "research", "decision",
    "manifest", "changelog", "backlog",
    "charter", "requirements", "specification", "log", "artifact",
    "course", "article", "book", "video",
    "plan", "prompt",
}
# Префикс имени → допустимые type. Единственное место этой таблицы.
PREFIX_TYPES = {
    "Политика": {"policy"}, "Стандарт": {"standard"}, "Процесс": {"workflow"}, "Ранбук": {"runbook"},
    "Плейбук": {"playbook"}, "Инструкция": {"instruction"}, "Методология": {"methodology"},
    "Справочник": {"reference"}, "Гайд": {"guide"}, "Бутстрап": {"bootstrap"}, "Шаблон": {"template"},
    "Аналитика": {"analytics"}, "Ретроспектива": {"retrospective"}, "Постмортем": {"postmortem"},
    "Аудит": {"audit"}, "Исследование": {"research"}, "Спецификация": {"specification"}, "Устав": {"charter"},
    "Требования": {"requirements"}, "Журнал": {"log"}, "Артефакт": {"artifact"},
    "План": {"plan"}, "Промпт": {"prompt"}, "Постановка": {"requirements", "charter"},
    "Выжимка": {"reference"}, "Сверка": {"reference"}, "NOTE-SRC": {"reference"},
    MANIFEST: {"manifest"},
}
OWNER_PREFIXES = {"Методология", "Стандарт", "Справочник", "Инструкция", "Плейбук", "Гайд", "Политика", "Ранбук"}
FOOTER_TYPES = {"policy", "standard", "workflow", "methodology", "guide", "reference", "runbook", "playbook"}
STATUSES = {"draft", "active", "deprecated", "superseded"}
REVIEW_CYCLES = {"1m", "3m", "6m", "12m", "never"}   # решение владельца 17.11.2026, без привязки к типу
REQUIRED = ("type", "status", "description", "author", "created", "updated", "review_cycle", "last_reviewed", "tags")
DATE_FIELDS = ("created", "updated", "last_reviewed")
MATURITY = {"active": "основа", "draft": "черновик"}
DESCRIPTION_LIMIT = 1024
FOOTER_LIMIT = 10
SKILL_PATTERNS = ("growth-*", "ads-*")
# Навыки с исходником вне канона (репозиторий компании, сторонние) и навыки, которые ставятся в проект.
EXTERNAL_SKILLS = {"impeccable", "blogger-analysis", "explainer-video", "fast-explainer", "dataviz", "find-skills",
                   "last30days", "research", "nmt-*"}
PASSPORT_HEADINGS = re.compile(r"место в системе|кто за что отвечает|^\d*[.\s]*границы", re.I)
PROJECT_NAMES = re.compile(r"Бризекс|Breezeks|viklimat|виклимат|Учебн(?:ый|ого|ом) центр|Л-Класс|Логистика с нуля|"
                           r"Поволжье|Anmarto|Lorentz|Taira|\b\d{8,10}\b", re.I)
ORIGIN_LINE = re.compile(r"обобщ|происхожд|из практики|источник", re.I)

RULES = {  # правило: (уровень, область) — doc: находка по документу; global: правило на пары документов
    "link-broken": ("error", "doc"), "link-wiki": ("error", "doc"), "link-absolute": ("error", "doc"),
    "link-outside-canon": ("error", "doc"), "section-missing": ("error", "doc"),
    "fm-missing": ("error", "doc"), "fm-yaml": ("error", "doc"), "fm-field": ("error", "doc"),
    "fm-status": ("error", "doc"), "fm-review-cycle": ("error", "doc"), "fm-type": ("error", "doc"),
    "type-prefix": ("error", "doc"), "name-prefix": ("warning", "doc"), "passport-block": ("error", "doc"),
    "manifest-catalog": ("error", "global"), "map-owner": ("error", "global"), "map-maturity": ("error", "global"),
    "skill-unknown": ("error", "global"), "skill-not-in-claude": ("error", "global"),
    "skill-description": ("error", "doc"), "skill-yaml": ("warning", "doc"), "skill-not-linked": ("warning", "global"),
    "skill-neighbors": ("warning", "global"), "owners-config": ("error", "global"), "owner-copy": ("warning", "doc"),
    "project-name": ("warning", "doc"), "footer-long": ("warning", "doc"), "footer-missing": ("warning", "doc"),
}

LINK = re.compile(r"!?\[(?P<label>(?:[^\[\]]|\[[^\[\]]*\])*)\]\((?P<target><[^<>\n]*>|[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)"
                  r"(?:\s+\"[^\"\n]*\")?\)")
WIKI = re.compile(r"\[\[[^\[\]\n]+\]\]")
NUM = r"\d+[а-яa-z]?(?:\.\d+)*"
SECREF = re.compile(rf"[ \t]*[,;:]?\s*(?:[—–]\s*)?(?P<word>раздел(?:ы|ах|ам|е|а)?|шаг(?:и|ах|е|а)?)"
                    rf"\s+(?P<ref>«[^»]{{1,160}}»|{NUM}(?:\s*(?:,|и|или|–|-)\s*{NUM})*)", re.I)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)


class Failure(Exception):
    """Сбой самого скрипта — код возврата 2."""


@dataclass
class Finding:
    rule: str
    path: str
    line: int
    message: str
    essence: str
    new: bool = True

    @property
    def level(self) -> str:
        return RULES[self.rule][0]

    @property
    def key(self) -> str:
        return f"{self.rule} | {self.path} | {self.essence}"

    def as_dict(self) -> dict:
        return {"rule": self.rule, "level": self.level, "path": self.path, "line": self.line,
                "message": self.message, "key": self.key, "new": self.new}


@dataclass
class Doc:
    path: Path
    rel: str
    text: str            # LF, без BOM
    fenced: str          # блоки кода заменены пробелами
    masked: str          # и блоки, и inline-код заменены пробелами
    kind: str            # core | raw | foreign | skill
    fm: dict | None = None
    fm_error: str | None = None
    targets: set = field(default_factory=set)   # normcase путей, на которые ссылается документ


@dataclass
class Report:
    findings: list
    counts: dict
    passport: tuple = (0, 0)


# ---------- чтение и разбор ----------

def read_text(path: Path) -> str:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Failure(f"{path}: не читается как UTF-8 ({exc})") from exc
    except OSError as exc:
        raise Failure(f"{path}: не читается ({exc})") from exc
    return text.lstrip("﻿").replace("\r\n", "\n")


def mask_code(text: str) -> tuple[str, str]:
    """(текст без блоков кода, текст без блоков и inline-кода); длина и переводы строк сохранены."""
    out = []
    fence = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        marker = stripped[:3]
        if fence is None and marker in ("```", "~~~"):
            fence = marker
            out.append(" " * len(line))
        elif fence is not None:
            if stripped.startswith(fence):
                fence = None
            out.append(" " * len(line))
        else:
            out.append(line)
    fenced = "\n".join(out)
    masked = re.sub(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", lambda m: " " * len(m.group(0)), fenced)
    return fenced, masked


def split_frontmatter(text: str) -> tuple[str | None, str | None]:
    """(блок frontmatter, ошибка). Нет frontmatter — (None, None)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), None
    return None, "frontmatter не закрыт строкой ---"


def parse_frontmatter(text: str) -> tuple[dict | None, str | None, bool]:
    """(поля, ошибка разбора, есть ли frontmatter)."""
    block, error = split_frontmatter(text)
    if error:
        return None, error, True
    if block is None:
        return None, None, False
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        return None, f"не строгий YAML: {str(exc).splitlines()[0]}", True
    if not isinstance(data, dict):
        return None, "frontmatter — не словарь полей", True
    return data, None, True


def loose_description(text: str) -> str:
    """Описание навыка без строгого YAML: строка или блочный скаляр после `description:`."""
    block, _ = split_frontmatter(text)
    if not block:
        return ""
    lines = block.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^description:\s*(.*)$", line)
        if not m:
            continue
        head = m.group(1).strip()
        if head and head[0] not in "|>":
            return head.strip("'\"")
        body = []
        for nxt in lines[i + 1:]:
            if nxt and not nxt[0].isspace():
                break
            body.append(nxt.strip())
        return "\n".join(body).strip()
    return ""


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[*`«»\"“”]", "", s)
    return re.sub(r"\s+", " ", s).strip(" .:")


def prefix_of(path: Path) -> str | None:
    stem = path.stem
    if path.name == MANIFEST:
        return MANIFEST
    if stem.startswith("NOTE-SRC"):
        return "NOTE-SRC"
    return stem.split(" — ")[0].strip() if " — " in stem else None


def ncase(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def under(path, folder) -> bool:
    p, f = ncase(path), ncase(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


# ---------- разделы ----------

class Sections:
    def __init__(self, text: str):
        fenced = text   # разделы ищутся и внутри блоков кода цели (бриф целиком в блоке): ложное «нет» хуже
        self.heads = [norm(m.group(2)) for m in HEADING.finditer(fenced)]
        self.cells = [norm(m.group(1)) for m in re.finditer(r"^\s*\|([^|\n]*)\|", fenced, re.M)]
        self.items = [norm(m.group(1)) for m in
                      re.finditer(r"^\s*(?:[-*+]\s+)?\**((?:шаг\s+)?" + NUM + r")[.)]", fenced, re.M | re.I)]
        self.bold = [norm(m.group(1)) for m in re.finditer(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?\*\*([^*\n]+)\*\*", fenced,
                                                           re.M)]

    def has_number(self, num: str) -> bool:
        pat = re.compile(rf"^(?:(?:раздел|шаг|часть|этап)\s+)?{re.escape(num.lower())}(?:[.):\s—–-]|$)")
        return any(pat.match(x) for x in self.heads + self.cells + self.items)

    def has_title(self, title: str) -> bool:
        t = norm(title)
        return bool(t) and any(t in x for x in self.heads + self.cells + self.bold)

    def has(self, ref: str) -> bool:
        ref = ref.strip()
        if ref.startswith("«"):
            return self.has_title(ref.strip("«»"))
        return self.has_number(ref)


def section_refs(ref: str) -> list[str]:
    if ref.startswith("«"):
        return [re.sub(r"\s+", " ", ref)]
    return re.findall(NUM, ref)


# ---------- сбор документов ----------

def kind_of(rel_parts: tuple) -> str | None:
    if rel_parts[0] == METHODS:
        if any(any(part.startswith(f) for f in FOREIGN) for part in rel_parts[1:-1]):
            return "foreign"
        return "raw" if RAW in rel_parts else "core"
    if rel_parts[:2] == SKILLS_SRC and len(rel_parts) == 4 and rel_parts[-1] == "SKILL.md":
        return "skill"
    return None


def collect(root: Path) -> list[Doc]:
    methods = root / METHODS
    if not methods.is_dir():
        raise Failure(f"нет папки {methods}: корень Knowledge-OS не найден (--root)")
    paths = sorted(methods.rglob("*.md"))
    skills_dir = root.joinpath(*SKILLS_SRC)
    if skills_dir.is_dir():
        paths += sorted(p / "SKILL.md" for p in skills_dir.iterdir() if (p / "SKILL.md").is_file())
    docs = []
    for path in paths:
        rel_parts = path.relative_to(root).parts
        kind = kind_of(rel_parts)
        if kind is None:
            continue
        text = read_text(path)
        fenced, masked = mask_code(text)
        doc = Doc(path=path, rel="/".join(rel_parts), text=text, fenced=fenced, masked=masked, kind=kind)
        if kind in ("core", "skill"):
            doc.fm, doc.fm_error, _ = parse_frontmatter(text)
        docs.append(doc)
    if not docs:
        raise Failure(f"в {methods} ноль документов — проверять нечего")
    return docs


# ---------- проверки ----------

def resolve(src: Path, target: str) -> tuple[str, Path | None]:
    t = target.strip()
    if t.startswith("<") and t.endswith(">"):
        t = t[1:-1].strip()
    if not t or t.startswith("#"):
        return "anchor", None
    t = t.split("#")[0]
    if re.match(r"^[A-Za-z]:[\\/]", t) or t.startswith(("/", "\\", "~")):
        return "absolute", Path(os.path.expanduser(t))
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", t):
        return "external", None
    path = Path(os.path.normpath(src.parent / t))
    if not path.exists() and "%" in t:
        alt = Path(os.path.normpath(src.parent / unquote(t)))
        if alt.exists():
            path = alt
    return "relative", path


def check_links(doc: Doc, root: Path, cache: dict, out: list):
    for m in WIKI.finditer(doc.masked):
        out.append(Finding("link-wiki", doc.rel, line_of(doc.text, m.start()), f"вики-ссылка {m.group(0)}",
                           m.group(0)))
    for m in LINK.finditer(doc.masked):
        target = m.group("target")
        line = line_of(doc.text, m.start())
        kind, path = resolve(doc.path, target)
        if kind in ("anchor", "external"):
            continue
        shown = target.strip("<>")
        if kind == "absolute":
            if doc.kind != "skill":
                out.append(Finding("link-absolute", doc.rel, line, f"абсолютный путь в ссылке: {shown}", shown))
                continue
            if not under(path, root):
                continue
        doc.targets.add(ncase(path))
        if not path.exists():
            out.append(Finding("link-broken", doc.rel, line, f"нет файла: {shown}", shown))
            continue
        if doc.kind == "core" and not any(under(path, root / r) for r in CANON_ROOTS):
            out.append(Finding("link-outside-canon", doc.rel, line,
                               f"ссылка из ядра за пределы канона: {shown}", shown))
        if path.is_file() and path.suffix == ".md" and path.name != MANIFEST:
            ref = SECREF.match(doc.masked, m.end())
            if ref:
                key = ncase(path)
                if key not in cache:
                    cache[key] = Sections(read_text(path))
                for one in section_refs(ref.group("ref")):
                    if not cache[key].has(one):
                        word = "раздел" if ref.group("word").lower().startswith("раздел") else "шаг"
                        out.append(Finding("section-missing", doc.rel, line,
                                           f"в «{path.stem}» нет: {word} {one}", f"{path.name} | {word} {one}"))


def fm_value(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return "" if value is None else str(value).strip()


def check_frontmatter(doc: Doc, out: list):
    if doc.fm_error:
        out.append(Finding("fm-yaml", doc.rel, 1, doc.fm_error, "yaml"))
        return
    if doc.fm is None:
        out.append(Finding("fm-missing", doc.rel, 1, "нет frontmatter", "нет frontmatter"))
        return
    fm = doc.fm
    for name in REQUIRED:
        if name not in fm:
            out.append(Finding("fm-field", doc.rel, 1, f"нет поля {name}", f"нет {name}"))
        elif name == "tags" and not fm[name]:
            out.append(Finding("fm-field", doc.rel, 1, "tags пустые", "пустые tags"))
    for name in DATE_FIELDS:
        if name in fm and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fm_value(fm[name])):
            out.append(Finding("fm-field", doc.rel, 1, f"{name}: не дата ГГГГ-ММ-ДД ({fm_value(fm[name])})",
                               f"{name} не дата"))
    status = fm_value(fm.get("status"))
    if "status" in fm and status not in STATUSES:
        out.append(Finding("fm-status", doc.rel, 1, f"status={status} вне списка {sorted(STATUSES)}", status))
    cycle = fm_value(fm.get("review_cycle"))
    if "review_cycle" in fm and cycle not in REVIEW_CYCLES:
        out.append(Finding("fm-review-cycle", doc.rel, 1, f"review_cycle={cycle} вне списка 1m/3m/6m/12m/never",
                           cycle))
    typ = fm_value(fm.get("type"))
    if "type" in fm and typ not in TYPES:
        out.append(Finding("fm-type", doc.rel, 1, f"type={typ} вне закрытого списка", typ))
    prefix = prefix_of(doc.path)
    if prefix is None or prefix not in PREFIX_TYPES:
        out.append(Finding("name-prefix", doc.rel, 1, f"префикс имени «{prefix or doc.path.stem}» не из словаря",
                           prefix or "нет префикса"))
    elif typ and typ not in PREFIX_TYPES[prefix]:
        out.append(Finding("type-prefix", doc.rel, 1,
                           f"type={typ} не совпадает с префиксом «{prefix}» ({'/'.join(sorted(PREFIX_TYPES[prefix]))})",
                           f"{typ} ≠ {prefix}"))


def is_owner(doc: Doc) -> bool:
    return doc.kind == "core" and prefix_of(doc.path) in OWNER_PREFIXES and APPLIED not in doc.rel.split("/")


def check_document_body(doc: Doc, out: list):
    heads = [(m.start(), len(m.group(1)), m.group(2)) for m in HEADING.finditer(doc.fenced)]
    if is_owner(doc) and doc.path.name != MAP_FILE:
        if not any(PASSPORT_HEADINGS.search(norm(h)) for _, _, h in heads):
            out.append(Finding("passport-block", doc.rel, 1, "нет блока «Место в системе»", "нет блока"))
    footer = [(pos, lvl) for pos, lvl, h in heads if norm(h) == "связанные документы"]
    typ = fm_value((doc.fm or {}).get("type"))
    if footer:
        pos, lvl = footer[-1]
        rest = doc.fenced[pos:].split("\n")[1:]
        items = 0
        for line in rest:
            if HEADING.match(line):
                break
            if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line):
                items += 1
        if items > FOOTER_LIMIT:
            out.append(Finding("footer-long", doc.rel, line_of(doc.text, pos), f"футер {items} ссылок > {FOOTER_LIMIT}",
                               "длинный футер"))
    elif typ in FOOTER_TYPES:
        out.append(Finding("footer-missing", doc.rel, 1, "нет футера «Связанные документы»", "нет футера"))
    if is_owner(doc):   # переносимое знание; планы и постановки законно называют первого потребителя
        start = 0
        block, _ = split_frontmatter(doc.text)
        if block is not None:
            start = block.count("\n") + 2
        for i, line in enumerate(doc.fenced.split("\n")[start:], start + 1):
            m = PROJECT_NAMES.search(line)
            if m and not ORIGIN_LINE.search(line):
                out.append(Finding("project-name", doc.rel, i, f"имя проекта или идентификатор «{m.group(0)}» в ядре",
                                   m.group(0)))


def nearest_manifest(doc: Doc, root: Path) -> Path | None:
    folder = doc.path.parent
    top = root / METHODS
    while under(folder, top):
        candidate = folder / MANIFEST
        if candidate.is_file():
            return candidate
        if ncase(folder) == ncase(top):
            return None
        folder = folder.parent
    return None


def named_in(text: str, path: Path) -> bool:
    """Документ назван в тексте полным именем или именем без хвоста в скобках («Методология — Лестница Ханта»)."""
    short = path.stem.split(" (")[0]
    return path.stem in text or (" — " in short and len(short) >= 15 and short in text)


def mentions(doc_index: dict, holder: Path, target: Path) -> bool:
    holder_doc = doc_index.get(ncase(holder))
    if holder_doc is None:
        text = read_text(holder)
        targets = set()
        for m in LINK.finditer(mask_code(text)[1]):
            kind, path = resolve(holder, m.group("target"))
            if kind == "relative":
                targets.add(ncase(path))
    else:
        text, targets = holder_doc.text, holder_doc.targets
    return ncase(target) in targets or named_in(text, target)


def check_catalogs(docs: list, root: Path, doc_index: dict, out: list):
    map_path = root / METHODS / MAP_FILE
    for doc in docs:
        if doc.kind != "core" or doc.path.name == MANIFEST:
            continue
        manifest = nearest_manifest(doc, root)
        if manifest is not None and not mentions(doc_index, manifest, doc.path):
            mrel = "/".join(manifest.relative_to(root).parts)
            out.append(Finding("manifest-catalog", doc.rel, 1, f"не назван в {mrel}", f"нет в {mrel}"))
    if not map_path.is_file():
        out.append(Finding("map-owner", f"{METHODS}/{MAP_FILE}", 0, "нет карты методобазы", "нет карты"))
        return
    map_doc = doc_index[ncase(map_path)]
    # Хозяина назначает карта области (карта маркетинга), остальное — карта методобазы: смотрим обе.
    maps = [map_doc] + [d for d in docs if d.kind == "core" and d.path.stem.startswith("Справочник — Карта")
                        and ncase(d.path) in map_doc.targets]
    targets = set().union(*(m.targets for m in maps))
    top = root / METHODS
    for doc in docs:
        if not is_owner(doc) or any(ncase(doc.path) == ncase(m.path) for m in maps):
            continue
        if ncase(doc.path) in targets or any(named_in(m.text, doc.path) for m in maps):
            continue
        folder, found = doc.path.parent, False
        while under(folder, top) and ncase(folder) != ncase(top):
            if ncase(folder / MANIFEST) in targets:
                found = True
                break
            folder = folder.parent
        if not found:
            out.append(Finding("map-owner", doc.rel, 1, "хозяин не назван в карте методобазы или карте области ни "
                                                        "прямо, ни через манифест папки", "нет в карте"))


# ---------- карта: колонка «Зрелость» ----------

def cell_spans(line: str) -> list[tuple[int, int]]:
    """Границы содержимого ячеек строки таблицы; `|` внутри кода и <пути> не делят ячейку."""
    pipes = []
    code = angle = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "`" and not angle:
            code = not code
        elif ch == "<" and not code and i > 0 and line[i - 1] == "(":
            angle = True
        elif ch == ">" and angle:
            angle = False
        elif ch == "|" and not code and not angle:
            pipes.append(i)
        i += 1
    spans = [(a + 1, b) for a, b in zip(pipes, pipes[1:])]
    if pipes and pipes[-1] < len(line.rstrip()) - 1:
        spans.append((pipes[-1] + 1, len(line)))
    return spans


def status_of(path: Path, doc_index: dict) -> str:
    doc = doc_index.get(ncase(path))
    fm = doc.fm if doc is not None and doc.fm is not None else parse_frontmatter(read_text(path))[0]
    return fm_value((fm or {}).get("status"))


def expected_maturity(cell: str, map_path: Path, doc_index: dict) -> str | None:
    found = []
    for m in LINK.finditer(mask_code(cell)[1]):
        kind, path = resolve(map_path, m.group("target"))
        if kind != "relative" or not path.is_file() or path.suffix != ".md":
            continue
        if path.name == MANIFEST:
            owners = [q for q in path.parent.glob("*.md") if prefix_of(q) in OWNER_PREFIXES]
            found.append("черновик" if any(status_of(q, doc_index) == "draft" for q in owners) else "основа")
        else:
            value = MATURITY.get(status_of(path, doc_index))
            if value:
                found.append(value)
    if not found:
        return None
    return "черновик" if "черновик" in found else "основа"


def maturity_rows(text: str, map_path: Path, doc_index: dict):
    """Строки карты с расхождением: (номер строки, начало, конец ячейки, задача, было, надо)."""
    header = None
    for no, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        if not line.lstrip().startswith("|"):
            header = None
            continue
        spans = cell_spans(line)
        cells = [line[a:b].strip() for a, b in spans]
        if header is None:
            if "Зрелость" in cells and "Документ-хозяин" in cells:
                header = (cells.index("Документ-хозяин"), cells.index("Зрелость"))
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        owner_col, mat_col = header
        if len(spans) <= max(owner_col, mat_col):
            continue
        expected = expected_maturity(cells[owner_col], map_path, doc_index)
        if expected and norm(cells[mat_col]) != expected:
            a, b = spans[mat_col]
            yield no, a, b, cells[0], cells[mat_col], expected


def check_map(root: Path, doc_index: dict, out: list):
    map_path = root / METHODS / MAP_FILE
    if not map_path.is_file():
        return
    rel = f"{METHODS}/{MAP_FILE}"
    for no, _, _, task, was, expected in maturity_rows(doc_index[ncase(map_path)].text, map_path, doc_index):
        out.append(Finding("map-maturity", rel, no, f"«{task[:70]}»: зрелость «{was}», по статусу — «{expected}»",
                           f"{norm(task)[:80]} → {expected}"))


def write_map(root: Path, doc_index: dict) -> list[str]:
    """Перезаписать только колонку «Зрелость»; остальной текст байт в байт."""
    map_path = root / METHODS / MAP_FILE
    if not map_path.is_file():
        raise Failure(f"нет карты {map_path}")
    raw = map_path.read_bytes().decode("utf-8")
    lines = raw.split("\n")
    report = []
    for no, a, b, task, was, expected in list(maturity_rows(raw, map_path, doc_index)):
        line = lines[no - 1]
        cell = line[a:b]
        lead = cell[:len(cell) - len(cell.lstrip())]
        trail = cell[len(cell.rstrip()):]
        lines[no - 1] = line[:a] + lead + expected + trail + line[b:]
        report.append(f"стр. {no}: «{task[:60]}» — «{was}» → «{expected}»")
    if report:
        with open(map_path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines))
    return report


# ---------- навыки ----------

SKILL_TOKEN = re.compile(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:-\*)?)`")


def skill_exists(name: str, sources: set, home: Path) -> bool:
    if name in EXTERNAL_SKILLS:
        return True
    if name.endswith("*"):
        stem = name[:-1]
        return any(s.startswith(stem) for s in sources) or (home.is_dir() and any(
            p.name.startswith(stem) for p in home.iterdir()))
    return name in sources or (home / name).exists()


def section_text(text: str, title_re: str) -> str:
    m = re.search(rf"^(#{{1,6}})\s+{title_re}.*$", text, re.M | re.I)
    if not m:
        return ""
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction = getattr(path, "is_junction", None)
    return bool(junction and junction())


def check_skills(docs: list, root: Path, home: Path, out: list):
    skills = {d.path.parent.name: d for d in docs if d.kind == "skill"}
    sources = set(skills)
    for name, doc in skills.items():
        if doc.fm_error or doc.fm is None:
            out.append(Finding("skill-yaml", doc.rel, 1, f"frontmatter: {doc.fm_error or 'нет'}", "yaml"))
            desc = loose_description(doc.text)
        else:
            desc = fm_value(doc.fm.get("description"))
        if len(desc) > DESCRIPTION_LIMIT:
            out.append(Finding("skill-description", doc.rel, 1,
                               f"описание {len(desc)} знаков > {DESCRIPTION_LIMIT}", f"описание > {DESCRIPTION_LIMIT}"))
        if not is_link(home / name):
            state = "нет" if not (home / name).exists() else "папка, а не ссылка"
            out.append(Finding("skill-not-linked", doc.rel, 0, f"в {home} не стоит ссылкой ({state})", "не ссылка"))
    claude = root / "CLAUDE.md"
    if claude.is_file():
        ctext = read_text(claude)
        named = set(SKILL_TOKEN.findall(ctext))
        for name, doc in skills.items():
            by_pattern = any(p in named and name.startswith(p[:-1]) for p in SKILL_PATTERNS)
            if name not in named and not by_pattern:
                out.append(Finding("skill-not-in-claude", doc.rel, 0, "навык не назван в CLAUDE.md корня",
                                   "нет в CLAUDE.md"))
        for token, line in tokens_with_lines(section_text(ctext, r"Скиллы"), ctext):
            if not skill_exists(token, sources, home):
                out.append(Finding("skill-unknown", "CLAUDE.md", line, f"навык `{token}` назван, но его нет", token))
    map_path = root / METHODS / MAP_FILE
    if map_path.is_file():
        mtext = read_text(map_path)
        header = None
        for no, line in enumerate(mtext.split("\n"), 1):
            if not line.lstrip().startswith("|"):
                header = None
                continue
            cells = [line[a:b] for a, b in cell_spans(line)]
            if header is None:
                header = next((i for i, c in enumerate(cells) if c.strip() == "Навык"), None)
                continue
            if header >= len(cells):
                continue
            for m in SKILL_TOKEN.finditer(cells[header]):
                if re.search(r"MCP\s*$", cells[header][:m.start()]):
                    continue
                if not skill_exists(m.group(1), sources, home):
                    out.append(Finding("skill-unknown", f"{METHODS}/{MAP_FILE}", no,
                                       f"навык `{m.group(1)}` назван в карте, но его нет", m.group(1)))
    named_in = {n: set(SKILL_TOKEN.findall(d.text)) for n, d in skills.items()}
    for name, doc in skills.items():
        bounds = section_text(doc.text, r"Границы")
        for other in sorted(set(SKILL_TOKEN.findall(bounds)) & sources - {name}):
            if name not in named_in[other]:
                out.append(Finding("skill-neighbors", doc.rel, 0,
                                   f"называет `{other}` в «Границах», а `{other}` не называет `{name}`", other))


def tokens_with_lines(section: str, full: str):
    offset = full.find(section) if section else -1
    for m in SKILL_TOKEN.finditer(section):
        yield m.group(1), line_of(full, offset + m.start()) if offset >= 0 else 0


# ---------- словарь хозяев ----------

def load_owners(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        concepts = data["concepts"]
        for c in concepts:
            for key in ("key", "owner", "anchor", "pattern"):
                if not isinstance(c.get(key), str) or not c[key]:
                    raise ValueError(f"у понятия {c.get('key')} нет поля {key}")
            c["_anchor"] = re.compile(c["anchor"])
            c["_pattern"] = re.compile(c["pattern"])
            sections = c.get("section")
            c["_sections"] = [] if sections is None else ([sections] if isinstance(sections, str) else list(sections))
            c["_aliases"] = list(c.get("aliases", []))
    except (OSError, ValueError, KeyError, TypeError, re.error) as exc:
        raise Failure(f"словарь хозяев {path} не разобран: {exc}") from exc
    return concepts


def check_owners(docs: list, root: Path, concepts: list, cache: dict, out: list):
    owners_rel = "kos_methodbase_owners.json"
    for c in concepts:
        owner = root / c["owner"]
        c["_path"] = owner
        if not owner.is_file():
            out.append(Finding("owners-config", owners_rel, 0, f"{c['key']}: нет документа-хозяина {c['owner']}",
                               f"{c['key']}: нет хозяина"))
            continue
        key = ncase(owner)
        if key not in cache:
            cache[key] = Sections(read_text(owner))
        for sec in c["_sections"]:
            ref = sec if re.fullmatch(NUM, sec) else f"«{sec}»"
            if not cache[key].has(ref):
                out.append(Finding("owners-config", owners_rel, 0, f"{c['key']}: у хозяина нет раздела {sec}",
                                   f"{c['key']}: нет раздела {sec}"))
    live = [c for c in concepts if c["_path"].is_file()]
    for doc in docs:
        if doc.kind not in ("core", "skill"):
            continue
        block, _ = split_frontmatter(doc.text)
        start = 0 if block is None else block.count("\n") + 2
        lines = doc.text.split("\n")
        for c in live:
            if ncase(doc.path) == ncase(c["_path"]):
                continue
            stem = c["_path"].stem
            for i in range(start, len(lines)):
                line = lines[i]
                if not (c["_anchor"].search(line) and c["_pattern"].search(line)):
                    continue
                if stem in line or any(a in line for a in c["_aliases"]):
                    continue
                if any(resolve(doc.path, m.group("target"))[1] is not None
                       and ncase(resolve(doc.path, m.group("target"))[1]) == ncase(c["_path"])
                       for m in LINK.finditer(line)):
                    continue
                snippet = re.sub(r"\s+", " ", line.strip())[:90]
                out.append(Finding("owner-copy", doc.rel, i + 1,
                                   f"{c['key']}: правило хозяина без ссылки на «{stem}»: {snippet}",
                                   f"{c['key']}: {snippet}"))


# ---------- прогон ----------

def run(root: Path, owners: Path, skills_home: Path) -> tuple[Report, list]:
    root = Path(root)
    concepts = load_owners(owners)
    docs = collect(root)
    doc_index = {ncase(d.path): d for d in docs}
    cache: dict = {}
    out: list = []
    for doc in docs:
        check_links(doc, root, cache, out)
        if doc.kind == "core":
            check_frontmatter(doc, out)
            check_document_body(doc, out)
    check_catalogs(docs, root, doc_index, out)
    check_map(root, doc_index, out)
    check_skills(docs, root, skills_home, out)
    check_owners(docs, root, concepts, cache, out)
    counts = Counter(d.kind for d in docs)
    owners_list = [d for d in docs if is_owner(d)]
    covered = sum(1 for d in owners_list
                  if any(PASSPORT_HEADINGS.search(norm(m.group(2))) for m in HEADING.finditer(d.fenced)))
    return Report(out, dict(counts), (covered, len(owners_list))), docs


def staged(root: Path) -> tuple[set, set]:
    """(изменённые и добавленные пути, удалённые и старые имена переименованных) из индекса git."""
    try:
        done = subprocess.run(["git", "diff", "--cached", "--name-status", "-z", "-M", "--relative"], cwd=root,
                              capture_output=True)
    except OSError as exc:
        raise Failure(f"git не запустился: {exc}") from exc
    if done.returncode != 0:
        raise Failure(f"git diff --cached не отработал: {done.stderr.decode('utf-8', 'replace').strip()}")
    parts = done.stdout.decode("utf-8").split("\0")
    changed, gone = set(), set()
    i = 0
    while i < len(parts) and parts[i]:
        status = parts[i]
        if status[0] in "RC":
            old, new = parts[i + 1], parts[i + 2]
            changed.add(ncase(root / new))
            if status[0] == "R":
                gone.add(ncase(root / old))
            i += 3
            continue
        path = parts[i + 1]
        (gone if status[0] == "D" else changed).add(ncase(root / path))
        i += 2
    return changed, gone


def select_changed(report: Report, docs: list, root: Path) -> int:
    changed, gone = staged(root)
    touched = changed | gone
    selected = {d.rel for d in docs if ncase(d.path) in changed or d.targets & touched}
    report.findings = [f for f in report.findings if RULES[f.rule][1] == "global" or f.path in selected]
    return len(selected)


def load_baseline(path: Path) -> Counter | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Counter({k: int(v) for k, v in data["errors"].items()})
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise Failure(f"база нарушений {path} не разобрана: {exc}") from exc


def mark_new(findings: list, baseline: Counter | None):
    left = Counter(baseline or {})
    for f in sorted(findings, key=lambda x: (x.key, x.line)):
        if f.level != "error":
            f.new = False
            continue
        if left[f.key] > 0:
            left[f.key] -= 1
            f.new = False


def write_baseline(path: Path, findings: list) -> int:
    errors = Counter(f.key for f in findings if f.level == "error")
    data = {"version": 1,
            "note": "Известные нарушения методобазы. Пересобирается только командой baseline по решению владельца.",
            "created": date.today().isoformat(),
            "errors": dict(sorted(errors.items()))}
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return sum(errors.values())


def summary(findings: list) -> dict:
    table: dict = {}
    for f in findings:
        row = table.setdefault(f.rule, {"level": f.level, "total": 0, "new": 0, "known": 0})
        row["total"] += 1
        if f.level == "error":
            row["new" if f.new else "known"] += 1
    return dict(sorted(table.items()))


def print_text(report: Report, mode: str, baseline_state: str, verbose: bool, selected: int | None):
    c = report.counts
    print(f"Методобаза: ядро {c.get('core', 0)}, исходные заметки {c.get('raw', 0)}, сторонние корпуса "
          f"{c.get('foreign', 0)}, навыков {c.get('skill', 0)}; режим {mode}"
          + (f"; документов в выборке --changed: {selected}" if selected is not None else ""))
    print(f"База нарушений: {baseline_state}")
    print(f"Блок «Место в системе»: {report.passport[0]} из {report.passport[1]} хозяев")
    table = summary(report.findings)
    if table:
        print(f"{'правило':22} {'уровень':14} {'новых':>6} {'из базы':>8} {'всего':>6}")
        for rule, row in table.items():
            level = "ошибка" if row["level"] == "error" else "предупреждение"
            print(f"{rule:22} {level:14} {row['new']:>6} {row['known']:>8} {row['total']:>6}")
    new = [f for f in report.findings if f.level == "error" and f.new]
    if new:
        print("\nНовые ошибки:")
        for f in sorted(new, key=lambda x: (x.path, x.line)):
            print(f"  ОШИБКА {f.path}:{f.line} [{f.rule}] {f.message}")
    shown: Counter = Counter()
    rest = [f for f in report.findings if not (f.level == "error" and f.new)]
    if rest:
        print("\nИз базы и предупреждения" + ("" if verbose else " (по 3 на правило; все — --verbose)") + ":")
        for f in sorted(rest, key=lambda x: (x.rule, x.path, x.line)):
            if not verbose and shown[f.rule] >= 3:
                continue
            shown[f.rule] += 1
            tag = "ошибка из базы" if f.level == "error" else "предупреждение"
            print(f"  {tag} {f.path}:{f.line} [{f.rule}] {f.message}")
    print(f"\nИтог: новых ошибок {len(new)}, ошибок из базы "
          f"{sum(1 for f in report.findings if f.level == 'error' and not f.new)}, "
          f"предупреждений {sum(1 for f in report.findings if f.level == 'warning')}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Проверка методобазы Knowledge-OS: ссылки, разделы, frontmatter, "
                                                 "карты, навыки, словарь хозяев")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="корень Knowledge-OS")
    parser.add_argument("--owners", default=str(DEFAULT_OWNERS), help="словарь хозяев (json)")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE), help="база известных нарушений (json)")
    parser.add_argument("--skills-home", default=str(DEFAULT_SKILLS_HOME), help="папка навыков агента")
    commands = parser.add_subparsers(dest="command", required=True)
    c = commands.add_parser("check", help="проверить; код 1 — есть ошибки сверх базы")
    c.add_argument("--changed", action="store_true", help="только изменённое в индексе git и то, что на него ссылается")
    c.add_argument("--json", action="store_true", help="вывод JSON")
    c.add_argument("--verbose", action="store_true", help="печатать все находки из базы и предупреждения")
    w = commands.add_parser("write", help="перезаписать колонку «Зрелость» карты методобазы по статусам")
    w.add_argument("--json", action="store_true", help="вывод JSON")
    commands.add_parser("baseline", help="записать текущие ошибки в базу нарушений")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        report, docs = run(root, Path(args.owners), Path(args.skills_home))
        if args.command == "write":
            lines = write_map(root, {ncase(d.path): d for d in docs})
            if args.json:
                print(json.dumps({"mode": "write", "changed": lines}, ensure_ascii=False, indent=1))
            else:
                print("\n".join(lines) if lines else "Колонка «Зрелость» совпадает со статусами — карта не изменена")
            return 0
        if args.command == "baseline":
            count = write_baseline(Path(args.baseline), report.findings)
            print(f"База нарушений записана: {args.baseline}, ошибок {count}")
            return 0
        selected = select_changed(report, docs, root) if args.changed else None
        baseline = load_baseline(Path(args.baseline))
    except Failure as exc:
        print(f"СБОЙ: {exc}")
        return 2
    mark_new(report.findings, baseline)
    state = "нет — все ошибки считаются новыми" if baseline is None else \
        f"{args.baseline} ({sum(baseline.values())} ошибок)"
    has_new = any(f.level == "error" and f.new for f in report.findings)
    code = 1 if has_new else 0
    if args.json:
        print(json.dumps({"mode": "check --changed" if args.changed else "check", "files": report.counts,
                          "selected": selected, "baseline": None if baseline is None else str(args.baseline),
                          "passport": {"covered": report.passport[0], "owners": report.passport[1]},
                          "summary": summary(report.findings),
                          "findings": [f.as_dict() for f in sorted(report.findings, key=lambda x: (x.path, x.line))],
                          "exit_code": code}, ensure_ascii=False, indent=1))
    else:
        print_text(report, "check --changed" if args.changed else "check", state, args.verbose, selected)
    return code


if __name__ == "__main__":
    sys.exit(main())
