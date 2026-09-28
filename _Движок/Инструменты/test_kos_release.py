"""Выпуск системы с кодом из канона в проект (ADR-0003, 28.09.2026): паспорт, запрет правок руками, отставание.

Каждый тест строит свой маленький канон — git-репозиторий с пакетом — и папку проекта во временном каталоге.
"""
import json
import subprocess
from pathlib import Path

import pytest

import kos_release
from kos_release import PASSPORT, ReleaseError, check, issue


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)


@pytest.fixture
def canon(tmp_path):
    """Канон с пакетом demo 1.2.0: модуль, вложенный модуль, файл данных и кэш, который в выпуск не идёт."""
    repo = tmp_path / "канон"
    root = repo / "Пакеты" / "demo"
    (root / "demo" / "sub").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.2.0"\n', encoding="utf-8")
    (root / "demo" / "__init__.py").write_text('"""demo"""\n', encoding="utf-8")
    (root / "demo" / "sub" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "demo" / "bridge.mjs").write_text("export const x = 1;\n", encoding="utf-8")
    (root / "demo" / "__pycache__").mkdir()
    (root / "demo" / "__pycache__" / "calc.cpython-314.pyc").write_bytes(b"\x00\x01")
    (repo / "Прочее.md").write_text("не пакет\n", encoding="utf-8")
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    commit(repo, "канон")
    return root


def released(tmp_path, canon) -> Path:
    into = tmp_path / "проект" / "demo"
    issue(canon, into, first=True)
    return into


def test_release_copies_the_package_and_writes_a_passport(tmp_path, canon):
    into = released(tmp_path, canon)
    passport = json.loads((into / PASSPORT).read_text(encoding="utf-8"))
    assert passport["package"] == "demo" and passport["version"] == "1.2.0"
    assert passport["canon_commit"] == git(canon.parents[1], "rev-parse", "HEAD")
    assert sorted(passport["files"]) == ["__init__.py", "bridge.mjs", "sub/calc.py"]
    assert (into / "sub" / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert not (into / "__pycache__").exists()


def test_fresh_release_passes_the_check(tmp_path, canon):
    report = check(released(tmp_path, canon), canon)
    assert report.ok and report.behind == 0 and report.version == "1.2.0"


def test_release_only_from_a_committed_canon(tmp_path, canon):
    (canon / "demo" / "sub" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="не закоммичен"):
        issue(canon, tmp_path / "проект" / "demo", first=True)


def test_hand_edit_in_the_release_is_found(tmp_path, canon):
    into = released(tmp_path, canon)
    (into / "sub" / "calc.py").write_text("def add(a, b):\n    return 42\n", encoding="utf-8")
    report = check(into)
    assert not report.ok and report.changed == ["sub/calc.py"]


def test_extra_and_missing_files_are_found(tmp_path, canon):
    into = released(tmp_path, canon)
    (into / "bridge.mjs").unlink()
    (into / "sub" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    report = check(into)
    assert not report.ok and report.missing == ["bridge.mjs"] and report.extra == ["sub/extra.py"]


def test_line_endings_are_not_an_edit(tmp_path, canon):
    """Git на Windows отдаёт рабочую копию с CRLF, сервер — с LF: это один и тот же выпуск."""
    into = released(tmp_path, canon)
    path = into / "sub" / "calc.py"
    text = path.read_bytes().replace(b"\r\n", b"\n")
    path.write_bytes(text)
    assert check(into).ok
    path.write_bytes(text.replace(b"\n", b"\r\n"))
    assert check(into).ok


def test_cache_in_the_release_is_ignored(tmp_path, canon):
    into = released(tmp_path, canon)
    (into / "sub" / "__pycache__").mkdir()
    (into / "sub" / "__pycache__" / "calc.cpython-314.pyc").write_bytes(b"\x00")
    assert check(into).ok


def test_lag_behind_the_canon_is_counted_only_for_the_package(tmp_path, canon):
    into = released(tmp_path, canon)
    repo = canon.parents[1]
    (repo / "Прочее.md").write_text("правка вне пакета\n", encoding="utf-8")
    commit(repo, "вне пакета")
    assert check(into, canon).behind == 0
    (canon / "demo" / "sub" / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    (canon / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.3.0"\n', encoding="utf-8")
    commit(repo, "пакет 1.3.0")
    report = check(into, canon)
    assert report.ok and report.behind == 1 and report.canon_version == "1.3.0"


def test_new_release_replaces_the_old_one(tmp_path, canon):
    into = released(tmp_path, canon)
    (canon / "demo" / "bridge.mjs").unlink()
    (canon / "demo" / "new.py").write_text("y = 2\n", encoding="utf-8")
    commit(canon.parents[1], "пакет без моста")
    issue(canon, into)
    assert not (into / "bridge.mjs").exists() and (into / "new.py").is_file() and check(into, canon).ok


def test_release_over_hand_edits_stops(tmp_path, canon):
    """Правку в выпуске не затирают молча: её сначала переносят в канон или отменяют."""
    into = released(tmp_path, canon)
    (into / "__init__.py").write_text('"""правка"""\n', encoding="utf-8")
    with pytest.raises(ReleaseError, match="правили руками"):
        issue(canon, into)


def test_first_release_over_an_existing_folder_needs_the_flag(tmp_path, canon):
    into = tmp_path / "проект" / "demo"
    into.mkdir(parents=True)
    (into / "__init__.py").write_text("старое\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="--first"):
        issue(canon, into)


def test_command_line_prints_counts_and_exit_codes(tmp_path, canon, capsys):
    into = tmp_path / "проект" / "demo"
    assert kos_release.main(["issue", "--package", str(canon), "--into", str(into), "--first"]) == 0
    assert "файлов: 3" in capsys.readouterr().out
    assert kos_release.main(["check", "--into", str(into), "--package", str(canon)]) == 0
    (into / "__init__.py").write_text("правка\n", encoding="utf-8")
    assert kos_release.main(["check", "--into", str(into)]) == 1
    assert "изменены: __init__.py" in capsys.readouterr().out
