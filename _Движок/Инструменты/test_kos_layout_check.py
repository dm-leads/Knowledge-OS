"""Тест kos_layout_check: каждое правило на маленьком git-репозитории."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kos_layout_check as lc  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "репо"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    (r / ".kos-layout.json").write_text(json.dumps({"docs_max": 3, "files_max": 4, "exceptions": "искл.txt",
                                                     "root_files": ["README.md", "искл.txt"]}), encoding="utf-8")
    return r


def put(repo, rel, text="x"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)


def rules(repo):
    return {(d, r) for d, r, _, _ in lc.layout_issues(repo)}


def test_thresholds_manifest_and_read_first(repo):
    for i in range(4):
        put(repo, f"01 — Проекты/П/Документ {i}.md")
    put(repo, "01 — Проекты/П/_manifest.md", "# П\n")
    got = rules(repo)
    assert ("01 — Проекты/П", "Р1") in got and ("01 — Проекты/П", "Р7") in got
    put(repo, "01 — Проекты/П/_manifest.md", "# П\nЧитать первым: Документ 0\nНе трогать: нет\n")
    assert ("01 — Проекты/П", "Р7") not in rules(repo)


def test_pictures_and_archive_exempt_snapshots_flagged(repo):
    for i in range(6):
        put(repo, f"02 — Области/Картинки/к{i}.png")
        put(repo, f"02 — Области/Архив/д{i}.md")
    put(repo, "02 — Области/Снимки страницы/блок.json")
    got = rules(repo)
    assert ("02 — Области/Картинки", "Р2") not in got and ("02 — Области/Архив", "Р1") not in got
    assert ("02 — Области/Снимки страницы", "Р4") in got


def test_root_whitelist_and_exception(repo):
    put(repo, "заметка.md")
    assert ("", "Р5") in rules(repo)
    for i in range(5):
        put(repo, f"06 — Инструменты/s{i}.py")
    put(repo, "искл.txt", "06 — Инструменты | Р2 | одна система | 2026-11-20\n")
    assert ("06 — Инструменты", "Р2") not in rules(repo)
