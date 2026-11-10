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
    assert "(slide2.xml)" in text


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


BS = chr(92)  # обратная косая; строки RTF собираем так, чтобы инструменты правки не трогали экранирование


def _rtf(tmp_path, body):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    (src / "x.rtf").write_bytes(body.replace("~", BS).encode("latin-1"))
    written, skipped, failed = k.extract(src, out)
    assert (written, failed) == (1, 0)
    return (out / "x.rtf.txt").read_text(encoding="utf-8")


def test_rtf_bin_inside_skipped_group_does_not_hang(tmp_path):
    assert "After" in _rtf(tmp_path, "{~rtf1{~pict~bin1 {}After}")


def test_rtf_bin_in_text_is_skipped(tmp_path):
    text = _rtf(tmp_path, "{~rtf1 A~bin2 {}B}")
    assert "A" in text and "B" in text


def test_rtf_surrogate_pair_joined(tmp_path):
    assert "\U0001F600" in _rtf(tmp_path, "{~rtf1~uc1~u-10179?~u-8704?}")


def test_rtf_fallback_control_symbol_suppressed(tmp_path):
    assert "\u03b1B" in _rtf(tmp_path, "{~rtf1~uc1~u945~~B}".replace("~~", "~" + "~"))


def test_rtf_fallback_stops_at_group_end(tmp_path):
    assert "\u03b1B" in _rtf(tmp_path, "{~rtf1{~uc1~u945}B}")


def test_rtf_font_charset_overrides_ansicpg(tmp_path):
    body = "{~rtf1~ansi~ansicpg1252{~fonttbl{~f0~fcharset204 Arial;}{~f1~fcharset0 Times;}}~f0 ~'cf~'f0~'e8~'e2~'e5~'f2 {~f1 ~'e9}}"
    text = _rtf(tmp_path, body)
    assert "\u041f\u0440\u0438\u0432\u0435\u0442" in text
    assert "\u00e9" in text


def _pptx_ordered(path):
    """Порядок слайдов задаёт presentation.xml (slide2, потом slide1); заметки и диаграмма — через связи слайда."""
    import zipfile
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    p = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    c = 'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
    rel = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
    def sld(body):
        return f'<p:sld {a} {p} {r}><p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>'
    def txt(*parts):
        return "<p:sp><p:txBody><a:p>" + "".join(parts) + "</a:p></p:txBody></p:sp>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", f'<p:presentation {p} {r}><p:sldIdLst>'
                   '<p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/></p:sldIdLst></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels", f'<Relationships {rel}>'
                   '<Relationship Id="rId1" Type="x/slide" Target="slides/slide1.xml"/>'
                   '<Relationship Id="rId2" Type="x/slide" Target="slides/slide2.xml"/></Relationships>')
        z.writestr("ppt/slides/slide1.xml", sld(txt("<a:r><a:t>Ответ</a:t></a:r>")))
        z.writestr("ppt/slides/slide2.xml", sld(txt("<a:r><a:t>Первый</a:t></a:r><a:br/><a:r><a:t>Второй</a:t></a:r>")))
        z.writestr("ppt/slides/slide9.xml", sld(txt("<a:r><a:t>Сирота</a:t></a:r>")))
        z.writestr("ppt/slides/_rels/slide1.xml.rels", f'<Relationships {rel}>'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide3.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/></Relationships>')
        z.writestr("ppt/notesSlides/notesSlide3.xml", sld(txt("<a:r><a:t>Заметка к ответу</a:t></a:r>")))
        z.writestr("ppt/charts/chart1.xml", f'<c:chartSpace {c} {a}><c:chart><c:title><c:tx><c:rich><a:p><a:r><a:t>Выручка</a:t></a:r></a:p></c:rich></c:tx></c:title>'
                   '<c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v>Январь</c:v></c:pt></c:strCache></c:strRef></c:cat>'
                   '<c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>42</c:v></c:pt></c:numCache></c:numRef></c:val></c:chart></c:chartSpace>')


def test_pptx_order_notes_charts_breaks(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    _pptx_ordered(src / "deck.pptx")
    written, skipped, failed = k.extract(src, out)
    assert (written, failed) == (1, 0)
    text = (out / "deck.pptx.txt").read_text(encoding="utf-8")
    assert text.index("Первый") < text.index("Ответ")
    assert "Первый\nВторой" in text
    assert text.index("Ответ") < text.index("Заметка к ответу")
    assert "Выручка" in text and "Январь" in text
    assert "Сирота" not in text


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
