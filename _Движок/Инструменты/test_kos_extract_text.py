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


def _make_pptx(path):
    """Минимальный pptx: два слайда (второй — с заметками), порядок слайдов — по номеру, не по имени."""
    import zipfile
    ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' \
         'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    def slide(*paras):
        body = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in paras)
        return f'<?xml version="1.0" encoding="UTF-8"?><p:sld {ns}><p:cSld><p:spTree><p:sp><p:txBody>' \
               f"{body}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/slides/slide10.xml", slide("Десятый"))
        z.writestr("ppt/slides/slide2.xml", slide("Баланс", "Активы &amp; пассивы"))
        z.writestr("ppt/notesSlides/notesSlide2.xml", slide("Заметка спикера"))


def test_pptx_slides_in_numeric_order_with_notes(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    _make_pptx(src / "lesson.pptx")
    written, skipped, failed = k.extract(src, out)
    assert (written, failed) == (1, 0)
    text = (out / "lesson.pptx.txt").read_text(encoding="utf-8")
    assert text.index("Баланс") < text.index("Десятый")
    assert "Активы & пассивы" in text
    assert "Заметка спикера" in text
    assert "=== слайд 2 ===" in text


RTF = (r"{\rtf1\ansi\ansicpg1251\deff0{\fonttbl{\f0 Times New Roman;}}{\colortbl;\red0\green0\blue0;}"
       r"{\*\generator Word;}{\info{\title HiddenTitle}}"
       r"\pard \'cf\'f0\'e8\'e2\'e5\'f2 \u1084?\u1080?\u1088? \par"
       r"{\pict\pngblip 89504e470d0a1a0a}"
       r"Who\~is\tab A\par }")


def test_rtf_decodes_cp1251_and_unicode_skips_pictures(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    (src / "book.rtf").write_text(RTF, encoding="ascii")
    written, skipped, failed = k.extract(src, out)
    assert (written, failed) == (1, 0)
    text = (out / "book.rtf.txt").read_text(encoding="utf-8")
    assert "Привет мир" in text
    assert "Who is\tA" in text
    assert "89504e47" not in text and "Times New Roman" not in text and "HiddenTitle" not in text


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
