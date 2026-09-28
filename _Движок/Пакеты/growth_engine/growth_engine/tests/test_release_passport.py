"""Выпуск движка в проекте не правится руками (ADR-0003 Knowledge-OS, 28.09.2026).

В каноне паспорта нет — тест пропускается. В проекте, получившем выпуск (`kos_release.py issue`), рядом с кодом лежит
`_паспорт_выпуска.json` с отпечатком каждого файла (sha256 текста с LF). Изменённый, пропавший или лишний файл —
красный набор: правка идёт в канон и приходит новым выпуском. Правила отбора файлов — те же, что у `kos_release.py`.
"""
import hashlib
import json
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
PASSPORT = PACKAGE / "_паспорт_выпуска.json"
SKIPPED_DIRS = {"__pycache__", ".pytest_cache"}


def release_files() -> dict[str, Path]:
    files = {}
    for path in sorted(PACKAGE.rglob("*")):
        relative = path.relative_to(PACKAGE)
        if not path.is_file() or path == PASSPORT or path.suffix in (".pyc", ".pyo"):
            continue
        if SKIPPED_DIRS.intersection(relative.parts):
            continue
        files[relative.as_posix()] = path
    return files


@pytest.mark.skipif(not PASSPORT.is_file(), reason="канон: паспорта выпуска нет, это исходник")
def test_release_matches_its_passport():
    passport = json.loads(PASSPORT.read_text(encoding="utf-8"))
    listed, actual = passport["files"], release_files()
    changed = [name for name in sorted(listed) if name in actual
               and hashlib.sha256(actual[name].read_bytes().replace(b"\r\n", b"\n")).hexdigest() != listed[name]]
    missing = [name for name in sorted(listed) if name not in actual]
    extra = [name for name in sorted(actual) if name not in listed]
    assert not (changed or missing or extra), (
        f"выпуск {passport['package']} {passport['version']} правили руками — изменены: {changed}, пропали: "
        f"{missing}, лишние: {extra}; правку внести в канон ({passport['canon_path']}) и сделать новый выпуск")
