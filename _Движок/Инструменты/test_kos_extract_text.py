"""Тесты kos_extract_text: запуск — из этой папки `py -3 -m pytest test_kos_extract_text.py`."""
import docx
import openpyxl
import pymupdf

import kos_extract_text as k


def _make_sources(src):
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Page one text")
    pdf.new_page()  # страница без текста
    pdf.save(src / "deck.pdf")

    d = docx.Document()
    d.add_paragraph("Абзац конспекта")
    d.add_table(rows=1, cols=2).rows[0].cells[0].text = "ячейка"
    (src / "sub").mkdir()
    d.save(src / "sub" / "SUMMARY.docx")

    wb = openpyxl.Workbook()
    wb.active["A1"] = "Задание"
    wb.save(src / "sheet.xlsx")

    (src / "video.mp4").write_bytes(b"\x00")


def test_extracts_all_supported_and_reports(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    _make_sources(src)

    written, skipped, failed = k.extract(src, out)

    assert (written, skipped, failed) == (3, 1, 0)
    assert "Page one text" in (out / "deck.pdf.txt").read_text(encoding="utf-8")
    assert "без текста 1" in capsys.readouterr().out
    assert "ячейка" in (out / "sub__SUMMARY.docx.txt").read_text(encoding="utf-8")
    assert "Задание" in (out / "sheet.xlsx.txt").read_text(encoding="utf-8")


def test_broken_file_counts_as_error_and_exit_code(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    (src / "broken.pdf").write_bytes(b"not a pdf")
    assert k.main(["x", str(src), str(out)]) == 1


def test_zero_written_is_error(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    (src / "only.mp4").write_bytes(b"\x00")
    assert k.main(["x", str(src), str(out)]) == 1
