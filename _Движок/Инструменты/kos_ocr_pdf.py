"""
Распознать pdf-сканы (страницы-картинки без текстового слоя) встроенным OCR Windows — шаг Ф2 стандарта извлечения знаний.

Запуск:  py -3 -I kos_ocr_pdf.py <pdf-или-папка> <папка-вывод> [--lang ru] [--dpi 200]

Страницы рендерит PyMuPDF во временную папку, текст распознаёт `kos_ocr_win.ps1` (Windows PowerShell 5.1,
Windows.Media.Ocr; язык должен стоять в Windows). Один .txt на pdf, вложенные пути склеиваются через «__» — как в
`kos_extract_text.py`; страницы размечены строкой «--- стр. N ---». По каждому pdf печатается строка описи: страниц,
пустых после распознавания, символов, прочитанных обратно. Выход 1 — сбой или ни одного записанного файла.
Платных вызовов нет. Сырьё курса и вывод держать вне git.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

PS1 = Path(__file__).with_name("kos_ocr_win.ps1")
MAX_SIDE = 9999  # Windows.Media.Ocr: OcrEngine.MaxImageDimension = 10000 пикселей по стороне


def out_name(rel: Path) -> str:
    return "__".join(rel.with_suffix(".txt").parts)


def page_dpi(width_pt: float, height_pt: float, dpi: int) -> int:
    """DPI, при котором длинная сторона страницы не превышает предел OCR Windows."""
    longest = max(width_pt, height_pt) / 72 * dpi
    return dpi if longest <= MAX_SIDE else int(dpi * MAX_SIDE / longest)


def render_pages(pdf: Path, tmp: Path, dpi: int) -> tuple[int, int]:
    import pymupdf

    reduced = 0
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc):
            d = page_dpi(page.rect.width, page.rect.height, dpi)
            reduced += d < dpi
            page.get_pixmap(dpi=d).save(tmp / f"p{i + 1:04d}.png")
        return len(doc), reduced


def run_ocr(tmp: Path, lang: str) -> None:
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1),
           "-InDir", str(tmp), "-Lang", lang]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace") or r.stdout.decode("utf-8", "replace"))


def assemble(tmp: Path, pages: int) -> tuple[str, int]:
    parts, empty = [], 0
    missing = [n for n in range(1, pages + 1) if not (tmp / f"p{n:04d}.txt").exists()]
    if missing:  # нет результата OCR — сбой, а не пустая страница
        raise RuntimeError(f"нет результата OCR для страниц {missing[:10]}")
    for n in range(1, pages + 1):
        text = (tmp / f"p{n:04d}.txt").read_text(encoding="utf-8").strip()
        if not text:
            empty += 1
        parts.append(f"--- стр. {n} ---\n{text}")
    return "\n\n".join(parts) + "\n", empty


def ocr_pdf(pdf: Path, out_file: Path, lang: str, dpi: int) -> str:
    with tempfile.TemporaryDirectory(prefix="kos_ocr_") as t:
        tmp = Path(t)
        pages, reduced = render_pages(pdf, tmp, dpi)
        run_ocr(tmp, lang)
        text, empty = assemble(tmp, pages)
    out_file.write_text(text, encoding="utf-8")
    back = out_file.read_text(encoding="utf-8")
    note = f", DPI снижен на {reduced} стр. (предел OCR)" if reduced else ""
    return f"{pages} стр., пустых {empty}{note}\t{len(back)} симв."


def main(argv):
    args = [a for a in argv[1:]]
    lang, dpi = "ru", 200
    if "--lang" in args:
        i = args.index("--lang"); lang = args[i + 1]; del args[i:i + 2]
    if "--dpi" in args:
        i = args.index("--dpi"); dpi = int(args[i + 1]); del args[i:i + 2]
    if len(args) != 2:
        print(__doc__)
        return 2
    src, out = Path(args[0]), Path(args[1])
    out.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        items = [(src, Path(src.name))]
    else:
        items = [(p, p.relative_to(src)) for p in sorted(src.rglob("*.pdf"))]
    names = {}
    for _, rel in items:  # «a/b.pdf» и «a__b.pdf» дали бы один файл — второй молча затёр бы первый
        names.setdefault(out_name(rel).lower(), []).append(str(rel))
    clashes = [v for v in names.values() if len(v) > 1]
    if clashes:
        for v in clashes:
            print(f"ОШИБКА\tодно имя вывода у файлов: {' | '.join(v)}")
        return 1
    written = failed = 0
    for pdf, rel in items:
        try:
            meta = ocr_pdf(pdf, out / out_name(rel), lang, dpi)
        except Exception as e:  # опись должна дойти до конца даже при сбое одного файла
            failed += 1
            print(f"ОШИБКА\t{rel}\t{type(e).__name__}: {e}")
            continue
        written += 1
        print(f"ок\t{rel}\t{meta}", flush=True)
    print(f"ИТОГ\tзаписано {written}\tошибок {failed}")
    return 1 if failed or not written else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
