"""Извлечь текст обучающего материала (pdf, docx, xlsx, pptx, rtf) в .txt — шаг Ф2 стандарта извлечения знаний.

Запуск:  py -3 -I kos_extract_text.py <папка-источник> <папка-вывод>

Один .txt на исходник; вложенные пути склеиваются через «__». По каждому файлу печатается строка описи:
статус, путь, объём (страниц / абзацев / листов / слайдов, страниц и слайдов без текста), число символов, прочитанных обратно.
Код выхода 1 — если был сбой чтения или не записано ни одного файла: ноль записанных — ошибка, а не «готово».
Сырьё курса в git не кладётся: папку вывода держать вне репозитория (временная папка сессии).
"""
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


def pptx_text(path):
    """Текст слайдов и заметок из XML внутри pptx (без python-pptx). Слайды — по номеру, не по имени файла."""
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

    def paras(xml):
        root = ET.fromstring(xml)
        out = []
        for p in root.iter(f"{ns}p"):
            line = "".join(t.text or "" for t in p.iter(f"{ns}t"))
            if line.strip():
                out.append(line)
        return out

    def num(name):
        m = re.search(r"(\d+)\.xml$", name)
        return int(m.group(1)) if m else 0

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=num)
        notes = {num(n): n for n in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)}
        parts, empty = [], 0
        for s in slides:
            body = paras(z.read(s))
            if not body:
                empty += 1
            parts.append(f"\n=== слайд {num(s)} ===\n" + "\n".join(body))
            if num(s) in notes:
                note = paras(z.read(notes[num(s)]))
                if note:
                    parts.append("--- заметки ---\n" + "\n".join(note))
    return "".join(parts), f"{len(slides)} слайд., без текста {empty}"


RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer", "headerl",
            "headerr", "footerl", "footerr", "listtable", "listoverridetable", "rsidtbl", "xmlnstbl",
            "themedata", "colorschememapping", "datastore", "latentstyles", "generator", "filetbl",
            "revtbl", "shppict", "nonshppict", "fldinst", "pgdsctbl"}
RTF_CHAR = {"par": "\n", "line": "\n", "sect": "\n\n", "page": "\n\n", "tab": "\t", "cell": " | ",
            "row": "\n", "emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘", "rquote": "’",
            "ldblquote": "«", "rdblquote": "»"}
RTF_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+", re.S)


def rtf_text(path):
    """Простой разбор RTF без внешних пакетов: \\'hh по кодовой странице, \\uN, пропуск картинок и служебных групп."""
    data = path.read_bytes()
    codepage = "cp1252"
    m = re.search(rb"\\ansicpg(\d+)", data[:4000])
    if m:
        codepage = f"cp{int(m.group(1))}"
    out, stack = [], []
    skip, uc, pending_skip = False, 1, 0
    hexbuf = bytearray()

    def flush_hex():
        if hexbuf:
            out.append(hexbuf.decode(codepage, errors="replace"))
            hexbuf.clear()

    pos, n = 0, len(data)
    while pos < n:
        # быстрый пропуск содержимого служебной группы (картинки — основной объём файла)
        if skip:
            depth = 0
            j = pos
            while j < n:
                c = data[j]
                if c == 0x5C:  # обратная косая — пропустить экранированный символ
                    j += 2
                    continue
                if c == 0x7B:
                    depth += 1
                elif c == 0x7D:
                    if depth == 0:
                        break
                    depth -= 1
                j += 1
            pos = j
            skip = False
            continue
        t = RTF_TOKEN.match(data, pos)
        if not t:
            pos += 1
            continue
        pos = t.end()
        word, arg, hexch, sym, brace, text = t.groups()
        if hexch is None:
            flush_hex()
        if pending_skip and (text is not None or hexch is not None):
            k = min(pending_skip, len(text) if text else 1)
            pending_skip -= k
            if text:
                text = text[k:]
            else:
                continue
        if brace == b"{":
            stack.append((skip, uc))
        elif brace == b"}":
            if stack:
                skip, uc = stack.pop()
        elif word is not None:
            w = word.decode()
            if w in RTF_SKIP:
                skip = True
            elif w == "uc" and arg:
                uc = int(arg)
            elif w == "u" and arg:
                code = int(arg)
                out.append(chr(code + 65536 if code < 0 else code))
                pending_skip = uc
            elif w in RTF_CHAR:
                out.append(RTF_CHAR[w])
        elif hexch is not None:
            hexbuf.append(int(hexch, 16))
        elif sym is not None:
            s = sym.decode("latin-1")
            if s == "*":
                skip = True
            elif s == "~":
                out.append(" ")
            elif s in "\\{}":
                out.append(s)
            elif s == "-":
                pass
        elif text:
            out.append(text.decode(codepage, errors="replace"))
    flush_hex()
    result = re.sub(r"[ \t]+\n", "\n", "".join(out))
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result, f"{len(result.splitlines())} строк, кодовая страница {codepage}"


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
