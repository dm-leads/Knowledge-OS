"""Извлечь текст обучающего материала (pdf, docx, xlsx) в .txt — шаг Ф2 стандарта извлечения знаний.

Запуск:  py -3 -I kos_extract_text.py <папка-источник> <папка-вывод>

Один .txt на исходник; вложенные пути склеиваются через «__». По каждому файлу печатается строка описи:
статус, путь, объём (страниц / абзацев / листов, страниц pdf без текста), число символов, прочитанных обратно.
Код выхода 1 — если был сбой чтения или не записано ни одного файла: ноль записанных — ошибка, а не «готово».
Сырьё курса в git не кладётся: папку вывода держать вне репозитория (временная папка сессии).
"""
import sys
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


HANDLERS = {".pdf": pdf_text, ".docx": docx_text, ".xlsx": xlsx_text}


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
