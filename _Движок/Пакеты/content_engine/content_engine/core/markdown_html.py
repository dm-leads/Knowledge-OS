"""Markdown статьи → HTML (Т20.2) без новых зависимостей: заголовки с якорями, абзацы, списки, таблицы, картинки,
ссылки, жирный и курсив. Якорь заголовка — транслит латиницей. Хватает для текста по эталону статьи; сложная вёрстка
проекта — в его сборщике из инстанса.
"""
from __future__ import annotations

import html
import re

from ..adapters.corpus import HEADING

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "j", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "h", "c", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
LIST = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)$")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")


def anchor(text: str) -> str:
    low = text.lower()
    out = "".join(TRANSLIT.get(ch, ch) for ch in low)
    return re.sub(r"[^a-z0-9]+", "-", out).strip("-")[:60] or "razdel"


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = IMAGE.sub(lambda m: f'<img src="{html.escape(m.group(2))}" alt="{html.escape(m.group(1))}">', text)
    text = LINK.sub(lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", text)


def _table(lines: list[str]) -> str:
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines
            if not re.match(r"^\|?\s*:?-{2,}", line.strip())]
    if not rows:
        return ""
    head = "<tr>" + "".join(f"<th>{inline(c)}</th>" for c in rows[0]) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
    return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"


def convert(body: str) -> tuple[str, list[dict]]:
    """HTML тела и заголовки [{level, text, id}]."""
    out, headings, used = [], [], set()
    para, items, kind, table = [], [], None, []

    def flush():
        nonlocal para, items, kind, table
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
        if items:
            tag = "ol" if kind == "ol" else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
        if table:
            out.append(_table(table))
        para, items, kind, table = [], [], None, []

    for line in body.splitlines():
        if not line.strip():
            flush()
            continue
        m = HEADING.match(line)
        if m:
            flush()
            level, text = len(m.group(1)), m.group(2).strip()
            hid = anchor(text)
            base, n = hid, 2
            while hid in used:
                hid, n = f"{base}-{n}", n + 1
            used.add(hid)
            headings.append({"level": level, "text": text, "id": hid})
            out.append(f'<h{level} id="{hid}">{inline(text)}</h{level}>')
            continue
        if line.strip().startswith("|"):
            if para or items:
                flush()
            table.append(line)
            continue
        lm = LIST.match(line)
        if lm:
            if para or table:
                flush()
            this = "ol" if lm.group(1)[0].isdigit() else "ul"
            if kind and kind != this:
                flush()
            kind = this
            items.append(lm.group(2))
            continue
        if items or table:
            flush()
        para.append(line.strip())
    flush()
    return "\n".join(out), headings


def plain(body: str) -> str:
    """Текст без разметки — эталон сверки «доехало»."""
    text = IMAGE.sub("", body)
    text = LINK.sub(r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"[*_`|]", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()
