import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import kos_ocr_pdf as k  # noqa: E402


def test_out_name_joins_nested_path():
    assert k.out_name(Path("a/b/c.pdf")) == "a__b__c.txt"


def test_assemble_marks_pages_and_counts_empty(tmp_path):
    (tmp_path / "p0001.txt").write_text("Первая", encoding="utf-8")
    (tmp_path / "p0002.txt").write_text("  ", encoding="utf-8")
    (tmp_path / "p0003.txt").write_text("", encoding="utf-8")
    text, empty = k.assemble(tmp_path, 3)
    assert "--- стр. 1 ---\nПервая" in text
    assert "--- стр. 3 ---" in text
    assert empty == 2


def test_assemble_missing_page_result_is_error(tmp_path):
    (tmp_path / "p0001.txt").write_text("Первая", encoding="utf-8")
    with pytest.raises(RuntimeError):
        k.assemble(tmp_path, 2)


def test_page_dpi_caps_long_side():
    assert k.page_dpi(595, 842, 200) == 200          # A4 — без изменений
    d = k.page_dpi(2384, 3370, 600)                    # A0 при 600 dpi
    assert 3370 / 72 * d <= k.MAX_SIDE


def test_output_name_clash_is_error(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "a__b.pdf").write_bytes(b"%PDF-1.4")
    assert k.main(["x", str(tmp_path), str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out" / "a__b.txt").exists()


def test_main_returns_1_when_nothing_written(tmp_path):
    assert k.main(["x", str(tmp_path), str(tmp_path / "out")]) == 1


def _ru_ocr_available() -> bool:
    if not shutil.which("powershell.exe"):
        return False
    cmd = ("[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null;"
           "[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | % { $_.LanguageTag }")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
    return "ru" in r.stdout


@pytest.mark.skipif(not _ru_ocr_available(), reason="нет OCR Windows с русским")
def test_end_to_end_scanned_pdf(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 120), "Sales department", fontsize=36)
    pix = page.get_pixmap(dpi=200)
    scan = pymupdf.open()
    img_page = scan.new_page(width=page.rect.width, height=page.rect.height)
    img_page.insert_image(img_page.rect, pixmap=pix)
    pdf = tmp_path / "scan.pdf"
    scan.save(pdf)
    out = tmp_path / "out"
    assert k.main(["x", str(pdf), str(out)]) == 0
    text = (out / "scan.txt").read_text(encoding="utf-8")
    assert "--- стр. 1 ---" in text
    assert "Sales" in text
