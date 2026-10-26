"""Разбор HTML стандартной библиотекой (Р19: без новых зависимостей): дерево, основной блок, мета, разметка.

Один разбор на все шаги: маркеры страниц конкурентов (Т18.5), «доехало» (Т22.1), автопроверки сборки (Т20.4) и проверка
разметки гейта статьи (Т20.1). Скрипты не исполняются. Основной блок — первый тег из `prepare.main_blocks`, иначе
`body` без `nav`, `header`, `footer`, `aside`, `script`, `style`. Разметка читается двумя видами: JSON-LD из
`<script type="application/ld+json">` и микроразметка (`itemscope`, `itemtype`, `itemprop`); микроразметка
приводится к тем же словарям, что JSON-LD, — так одна проверка сверяет оба формата.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP_ALWAYS = {"script", "style", "noscript", "template", "svg"}
CHROME = {"nav", "header", "footer", "aside"}
BLOCK = {"p", "div", "section", "article", "main", "li", "ul", "ol", "table", "tr", "td", "th", "h1", "h2", "h3", "h4",
         "h5", "h6", "br", "blockquote", "figure", "figcaption", "dd", "dt", "pre", "header", "footer"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent=None):
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent = parent

    def iter(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def find_all(self, *tags):
        return [n for n in self.iter() if n.tag in tags]

    def text(self, skip=frozenset()) -> str:
        parts: list[str] = []
        _collect(self, parts, SKIP_ALWAYS | set(skip))
        return re.sub(r"[ \t ]+", " ", re.sub(r"\s*\n\s*", "\n", "".join(parts))).strip()


def _collect(node: Node, parts: list, skip: set):
    if node.tag in skip:
        return
    if node.tag in BLOCK:
        parts.append("\n")
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        else:
            _collect(child, parts, skip)
    if node.tag in BLOCK:
        parts.append("\n")


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


class HtmlDoc:
    def __init__(self, html: str, main_blocks=("article", "main")):
        builder = _Builder()
        builder.feed(html or "")
        builder.close()
        self.root = builder.root
        self.main_blocks = tuple(main_blocks)

    # --- мета
    def _meta(self, name: str) -> str:
        for node in self.root.find_all("meta"):
            if node.attrs.get("name", "").lower() == name or node.attrs.get("property", "").lower() == name:
                return node.attrs.get("content", "").strip()
        return ""

    @property
    def title(self) -> str:
        found = self.root.find_all("title")
        return found[0].text() if found else ""

    @property
    def description(self) -> str:
        return self._meta("description")

    @property
    def robots(self) -> str:
        return self._meta("robots")

    @property
    def og_image(self) -> str:
        return self._meta("og:image")

    @property
    def canonical(self) -> str:
        for node in self.root.find_all("link"):
            if "canonical" in node.attrs.get("rel", "").lower().split():
                return node.attrs.get("href", "").strip()
        return ""

    def headings(self, level: int) -> list[str]:
        return [n.text() for n in self.root.find_all(f"h{level}") if n.text()]

    # --- основной блок
    def main(self) -> tuple[Node, str]:
        """(узел, как найден): первый тег из main_blocks, иначе body без меню и подвала."""
        for tag in self.main_blocks:
            found = self.root.find_all(tag)
            if found:
                return found[0], tag
        body = self.root.find_all("body")
        return (body[0] if body else self.root), "body"

    def main_text(self) -> str:
        node, how = self.main()
        return node.text(skip=CHROME if how == "body" else ())

    def markers(self) -> dict:
        """Маркеры страницы (Т18.5): заголовки, длина основного текста, таблицы, картинки, списки, FAQ."""
        node, how = self.main()
        text = node.text(skip=CHROME if how == "body" else ())
        h2 = [n.text() for n in node.find_all("h2") if n.text()]
        h3 = [n.text() for n in node.find_all("h3") if n.text()]
        faq = any(re.search(r"вопрос|faq|q&a", h, re.IGNORECASE) for h in h2 + h3) or \
            any(n.get("@type") == "FAQPage" for n in self.schema_nodes())
        return {"main": how, "h2": h2, "h3": h3, "chars": len(re.sub(r"\s+", "", text)),
                "tables": len(node.find_all("table")), "images": _content_images(node),
                "lists": len(node.find_all("ul", "ol")), "faq": faq, "text": text}

    def ids(self) -> set[str]:
        return {n.attrs["id"] for n in self.root.iter() if n.attrs.get("id")}

    def links(self) -> list[str]:
        return [n.attrs.get("href", "") for n in self.root.find_all("a") if n.attrs.get("href")]

    def tables(self) -> list[list[int]]:
        """Число ячеек в каждой строке каждой таблицы."""
        return [[len(tr.find_all("td", "th")) for tr in table.find_all("tr")] for table in self.root.find_all("table")]

    def select(self, selector: str) -> list[Node]:
        """Простой селектор: «тег», «.класс», «#id», «тег.класс»."""
        m = re.fullmatch(r"([a-z0-9]*)(?:([.#])([\w-]+))?", selector.strip().lower())
        if not m:
            return []
        tag, kind, name = m.groups()
        if not tag and not kind:
            return []
        out = []
        for n in self.root.iter():
            if tag and n.tag != tag:
                continue
            if kind == "." and name not in n.attrs.get("class", "").lower().split():
                continue
            if kind == "#" and n.attrs.get("id", "").lower() != name:
                continue
            if n is not self.root:
                out.append(n)
        return out

    # --- разметка
    def json_ld(self) -> list:
        out = []
        for node in self.root.find_all("script"):
            if node.attrs.get("type", "").lower() == "application/ld+json":
                raw = "".join(c for c in node.children if isinstance(c, str))
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError as exc:
                    out.append(exc)
        return out

    def microdata(self) -> list[dict]:
        items: list[dict] = []
        _microdata(self.root, None, items)
        return items

    def schema_nodes(self) -> list[dict]:
        """Узлы разметки обоих видов одним списком словарей с «@type»."""
        nodes = []
        for block in self.json_ld():
            if isinstance(block, Exception):
                continue
            nodes += flatten(block)
        for item in self.microdata():
            nodes += _nested(item)                 # FAQPage микроразметки часто вложен в Article свойством
        return nodes


def _nested(item: dict) -> list[dict]:
    out = [item]
    for value in item.values():
        for v in (value if isinstance(value, list) else [value]):
            if isinstance(v, dict) and v.get("@type"):
                out += _nested(v)
    return out


def _content_images(node: Node) -> int:
    """Картинки тела статьи: внутри общего предка разделов H2 (обложка над статьёй, баннеры и меню — вне его), без
    значков (ширина или высота до 64), векторных логотипов и встроенных data:-картинок."""
    heads = node.find_all("h2")
    root = node
    if len(heads) >= 2:
        chains = []
        for h in heads:
            chain, a = [], h.parent
            while a is not None:
                chain.append(a)
                a = a.parent
            chains.append(chain)
        common = [a for a in chains[0] if all(any(a is b for b in c) for c in chains[1:])]
        root = common[0] if common else node
    count = 0
    for img in root.find_all("img"):
        src = img.attrs.get("src", "").lower()
        sizes = [int(v) for v in (img.attrs.get("width", ""), img.attrs.get("height", "")) if v.isdigit()]
        if src.startswith("data:") or src.endswith(".svg") or (sizes and min(sizes) <= 64):
            continue
        count += 1
    return count


def flatten(block) -> list[dict]:
    if isinstance(block, list):
        return [n for b in block for n in flatten(b)]
    if not isinstance(block, dict):
        return []
    if "@graph" in block:
        return flatten(block["@graph"])
    return [block]


def _type(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1] if value else ""


def _value(node: Node):
    for attr in ("content", "datetime", "href", "src", "value"):
        if attr in node.attrs and (node.tag in ("meta", "time", "a", "link", "img", "data") or attr == "content"):
            return node.attrs[attr]
    return node.text()


def _microdata(node: Node, item: dict | None, top: list):
    for child in node.children:
        if not isinstance(child, Node):
            continue
        prop = child.attrs.get("itemprop")
        if "itemscope" in child.attrs:
            nested = {"@type": _type(child.attrs.get("itemtype", ""))}
            _microdata(child, nested, top)
            if prop and item is not None:
                _put(item, prop, nested)
            else:
                top.append(nested)
            continue
        if prop and item is not None:
            _put(item, prop, _value(child))
        _microdata(child, item, top)


def _put(item: dict, prop: str, value):
    for name in prop.split():
        if name in item:
            item[name] = item[name] if isinstance(item[name], list) else [item[name]]
            item[name].append(value)
        else:
            item[name] = value
