"""Извлечь текст обучающего материала (pdf, docx, xlsx, pptx, rtf) в .txt — шаг Ф2 стандарта извлечения знаний.

Запуск:  py -3 -I kos_extract_text.py <папка-источник> <папка-вывод>

Один .txt на исходник; вложенные пути склеиваются через «__». По каждому файлу печатается строка описи:
статус, путь, объём (страниц / абзацев / листов / слайдов, страниц и слайдов без текста), число символов, прочитанных обратно.
Код выхода 1 — если был сбой чтения или не записано ни одного файла: ноль записанных — ошибка, а не «готово».
Сырьё курса в git не кладётся: папку вывода держать вне репозитория (временная папка сессии).
"""
import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import docx
import openpyxl
import pymupdf


def pdf_text(path):
    doc = pymupdf.open(path)
    parts, empty = [], 0
    for i, page in enumerate(doc, 1):
        text = page.get_text().strip()
        if not text:
            empty += 1
        parts.append(f"\n=== стр. {i} ===\n{text}")
    return "".join(parts), f"{doc.page_count} стр., без текста {empty}"


def docx_text(path):
    d = docx.Document(path)
    lines = [p.text for p in d.paragraphs]
    for ti, table in enumerate(d.tables, 1):
        lines.append(f"\n=== таблица {ti} ===")
        for row in table.rows:
            lines.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(lines), f"{len(d.paragraphs)} абз., {len(d.tables)} табл."


def xlsx_text(path):
    wb = openpyxl.load_workbook(path, data_only=False)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"\n=== лист «{ws.title}» {ws.max_row}x{ws.max_column} ===")
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v) for v in row]
            if any(vals):
                lines.append(" | ".join(vals).rstrip(" |"))
    return "\n".join(lines), f"{len(wb.worksheets)} лист."


NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
NS_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _pptx_paras(xml):
    """Абзацы DrawingML по порядку документа: текст runs и полей, a:br — перевод строки."""
    root = ET.fromstring(xml)
    out = []
    for p in root.iter(f"{NS_A}p"):
        buf = []
        for el in p.iter():
            if el.tag == f"{NS_A}t":
                buf.append(el.text or "")
            elif el.tag == f"{NS_A}br":
                buf.append("\n")
        line = "".join(buf)
        if line.strip():
            out.append(line)
    return out


def _pptx_chart(xml):
    """Подписи диаграммы: заголовки и названия (a:t) и текстовые кэши категорий и серий (c:strCache)."""
    root = ET.fromstring(xml)
    out = [line for line in _pptx_paras(xml)]
    for cache in root.iter(f"{NS_C}strCache"):
        out += [v.text for v in cache.iter(f"{NS_C}v") if v.text and v.text.strip()]
    return out


def _rels(z, part):
    """Связи части: {rId: (тип, путь в архиве)}."""
    folder, name = part.rsplit("/", 1)
    rels_name = f"{folder}/_rels/{name}.rels"
    if rels_name not in z.namelist():
        return {}
    result = {}
    for r in ET.fromstring(z.read(rels_name)).iter(f"{NS_REL}Relationship"):
        target = r.get("Target", "")
        if r.get("TargetMode") == "External":
            continue
        path = posixpath.normpath(posixpath.join(folder, target)) if not target.startswith("/") else target[1:]
        result[r.get("Id")] = (r.get("Type", ""), path)
    return result


def pptx_text(path):
    """Текст слайдов, заметок и диаграмм из XML внутри pptx (без python-pptx).

    Порядок слайдов — как в презентации (p:sldIdLst); слайды вне списка (скрытые остатки) не берутся. Нет списка —
    по номеру файла. Заметки и диаграммы — через связи слайда, а не по совпадению номера.
    """
    def num(name):
        m = re.search(r"(\d+)\.xml$", name)
        return int(m.group(1)) if m else 0

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        slides = []
        if "ppt/presentation.xml" in names:
            rels = _rels(z, "ppt/presentation.xml")
            for sid in ET.fromstring(z.read("ppt/presentation.xml")).iter(f"{NS_P}sldId"):
                target = rels.get(sid.get(f"{NS_R}id"), ("", ""))[1]
                if target in names:
                    slides.append(target)
        if not slides:
            slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=num)
        parts, empty = [], 0
        for i, s in enumerate(slides, 1):
            body = _pptx_paras(z.read(s))
            extra = []
            rels = _rels(z, s)
            if not rels:  # нет файла связей — заметки по совпадению номера (старые и упрощённые файлы)
                guess = f"ppt/notesSlides/notesSlide{num(s)}.xml"
                if guess in names:
                    rels = {"n": ("/notesSlide", guess)}
            for rtype, target in rels.values():
                if target not in names:
                    continue
                if rtype.endswith("/chart"):
                    extra += _pptx_chart(z.read(target))
                elif rtype.endswith("/notesSlide"):
                    note = _pptx_paras(z.read(target))
                    if note:
                        extra.append("--- заметки ---\n" + "\n".join(note))
            if not body and not extra:
                empty += 1
            parts.append(f"\n=== слайд {i} ({posixpath.basename(s)}) ===\n" + "\n".join(body + extra))
    return "".join(parts), f"{len(slides)} слайд., без текста {empty}"


RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer", "headerl",
            "headerr", "footerl", "footerr", "listtable", "listoverridetable", "rsidtbl", "xmlnstbl",
            "themedata", "colorschememapping", "datastore", "latentstyles", "generator", "filetbl",
            "revtbl", "shppict", "nonshppict", "fldinst", "pgdsctbl"}
RTF_CHAR = {"par": "\n", "line": "\n", "sect": "\n\n", "page": "\n\n", "tab": "\t", "cell": " | ",
            "row": "\n", "emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘", "rquote": "’",
            "ldblquote": "«", "rdblquote": "»"}
RTF_CHARSET = {0: None, 1: None, 204: "cp1251", 238: "cp1250", 161: "cp1253", 162: "cp1254", 177: "cp1255",
               178: "cp1256", 186: "cp1257", 163: "cp1258", 222: "cp874", 128: "cp932", 134: "cp936",
               129: "cp949", 136: "cp950"}
# \x5c — обратная косая (так шаблоны не портятся при правке файла инструментами)
RTF_TOKEN = re.compile(rb"\x5c([a-zA-Z]+)(-?\d+)? ?|\x5c'([0-9a-fA-F]{2})|\x5c(.)|([{}])|([^\x5c{}\r\n]+)|[\r\n]+", re.S)
RTF_BIN = re.compile(rb"\x5cbin(\d+) ?")
RTF_FONT = re.compile(rb"\x5cf(\d+)((?:(?!\x5cf\d)[^;{}])*)")
RTF_FCHARSET = re.compile(rb"\x5cfcharset(\d+)")


def _rtf_fonts(data):
    """Кодовая страница каждого шрифта из \\fonttbl: {номер шрифта: кодировка}."""
    i = data.find(rb"\x5cfonttbl".replace(rb"\x5c", b"\x5c"))
    if i < 0:
        return {}
    depth, j = 0, i
    while j < len(data):
        c = data[j]
        if c == 0x5C:
            j += 2
            continue
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            if depth == 0:
                break
            depth -= 1
        j += 1
    fonts = {}
    for m in RTF_FONT.finditer(data[i:j]):
        cs = RTF_FCHARSET.search(m.group(2))
        if cs and RTF_CHARSET.get(int(cs.group(1))):
            fonts[int(m.group(1))] = RTF_CHARSET[int(cs.group(1))]
    return fonts


def _rtf_skip_group(data, pos):
    """Конец служебной группы, начиная с pos (внутри неё); \\binN пропускается по длине. Нет конца — len(data)."""
    depth, j, n = 0, pos, len(data)
    while j < n:
        c = data[j]
        if c == 0x5C:
            b = RTF_BIN.match(data, j)
            if b:
                j = b.end() + int(b.group(1))
                continue
            j += 2
            continue
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            if depth == 0:
                return j
            depth -= 1
        j += 1
    return n


def rtf_text(path):
    """Разбор RTF без внешних пакетов.

    Байты текста и \\'hh декодируются кодовой страницей текущего шрифта (\\fcharset), иначе \\ansicpg; \\uN — с пропуском
    \\ucN заменителей (они не переходят границу группы); суррогатные пары склеиваются; картинки, \\binN и служебные
    группы пропускаются.
    """
    data = path.read_bytes()
    default = "cp1252"
    m = re.search(rb"\x5cansicpg(\d+)", data[:4000])
    if m:
        default = f"cp{int(m.group(1))}"
    fonts = _rtf_fonts(data)
    out, stack = [], []
    skip, uc, font, pending = False, 1, None, 0
    buf = bytearray()
    buf_cp = default
    used = set()

    def cp():
        return fonts.get(font, default)

    def flush():
        if buf:
            out.append(buf.decode(buf_cp, errors="replace"))
            buf.clear()

    def add_bytes(b):
        nonlocal buf_cp
        if buf and buf_cp != cp():
            flush()
        buf_cp = cp()
        used.add(buf_cp)
        buf.extend(b)

    pos, n = 0, len(data)
    while pos < n:
        if skip:
            pos = _rtf_skip_group(data, pos)
            skip = False
            continue
        b = RTF_BIN.match(data, pos)
        if b:
            flush()
            pos = b.end() + int(b.group(1))
            continue
        t = RTF_TOKEN.match(data, pos)
        if not t:
            pos += 1
            continue
        pos = t.end()
        word, arg, hexch, sym, brace, text = t.groups()
        if brace is not None:
            pending = 0
            flush()
            if brace == b"{":
                stack.append((skip, uc, font))
            elif stack:
                skip, uc, font = stack.pop()
            continue
        if pending:
            if text:
                k = min(pending, len(text))
                pending -= k
                text = text[k:]
                if not text:
                    continue
            elif hexch is not None or sym is not None or (word is not None and word.decode() in RTF_CHAR):
                pending -= 1
                continue
        if word is not None:
            flush()
            w = word.decode()
            if w in RTF_SKIP:
                skip = True
            elif w == "uc" and arg is not None:
                uc = int(arg)
            elif w == "u" and arg is not None:
                code = int(arg)
                out.append(chr(code + 65536 if code < 0 else code))
                pending = uc
            elif w == "f" and arg is not None:
                font = int(arg)
            elif w in RTF_CHAR:
                out.append(RTF_CHAR[w])
        elif hexch is not None:
            add_bytes(bytes([int(hexch, 16)]))
        elif sym is not None:
            flush()
            s = sym.decode("latin-1")
            if s == "*":
                skip = True
            elif s == "~":
                out.append(" ")
            elif s in "\x5c{}":
                out.append(s)
        elif text:
            add_bytes(text)
    flush()
    result = "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", errors="replace")
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result, f"{len(result.splitlines())} строк, кодовые страницы {', '.join(sorted(used)) or default}"


HANDLERS = {".pdf": pdf_text, ".docx": docx_text, ".xlsx": xlsx_text, ".pptx": pptx_text, ".rtf": rtf_text}


def extract(src, out):
    """Вернуть (записано, пропущено, ошибок) и напечатать опись."""
    src, out = Path(src), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = skipped = failed = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        handler = HANDLERS.get(f.suffix.lower())
        if handler is None:
            skipped += 1
            print(f"ПРОПУЩЕН\t{rel}\tтип {f.suffix or 'без расширения'}")
            continue
        try:
            text, meta = handler(f)
        except Exception as e:  # noqa: BLE001 — опись должна дойти до конца
            failed += 1
            print(f"ОШИБКА\t{rel}\t{type(e).__name__}: {e}")
            continue
        target = out / (str(rel).replace("\\", "__").replace("/", "__") + ".txt")
        target.write_text(text, encoding="utf-8")
        back = target.read_text(encoding="utf-8")
        written += 1
        print(f"ок\t{rel}\t{meta}\t{len(back)} симв.")
    print(f"ИТОГ\tзаписано {written}\tпропущено {skipped}\tошибок {failed}")
    return written, skipped, failed


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) != 3:
        print(__doc__)
        return 2
    written, _, failed = extract(argv[1], argv[2])
    return 1 if failed or written == 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
