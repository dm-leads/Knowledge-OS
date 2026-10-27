"""Снимки каталога по версиям: запись, отказ переписать вышедшую версию, сверка выпуска со снимком и паспортом."""
import json
import subprocess
from pathlib import Path

import pytest

import kos_catalog_check as kcc
import kos_release

CLI = '''import json, sys
OPS = {ops!r}
def main(argv):
    if argv[1] == "--json":
        print(json.dumps({{"operations": [{{"command": c, "name": n}} for c, n in OPS.items()]}}, ensure_ascii=False))
    else:
        print("# каталог\\n" + "\\n".join(f"## `{{c}}`" for c in OPS))
    return 0
'''


def make_package(root: Path, version: str, ops: dict) -> Path:
    package = root / "toy_engine"
    code = package / "toy_engine"
    code.mkdir(parents=True, exist_ok=True)
    (package / "pyproject.toml").write_text(f'[project]\nname = "toy_engine"\nversion = "{version}"\n', encoding="utf-8")
    (code / "__init__.py").write_text("", encoding="utf-8")
    (code / "contracts.yaml").write_text("operations: {}\n", encoding="utf-8")
    (code / "cli.py").write_text(CLI.format(ops=ops), encoding="utf-8")
    return package


def commit(repo: Path, message: str):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", message], cwd=repo, check=True,
                   capture_output=True)


@pytest.fixture
def canon(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    package = make_package(tmp_path, "1.0.0", {"status": "что с машиной"})
    commit(tmp_path, "1.0.0")
    return package


def test_write_then_check_canon_code(tmp_path, canon):
    snaps = tmp_path / "снимки"
    assert kcc.write(canon, snaps) == ["1.0.0.json: записан", "1.0.0.md: записан"]
    assert kcc.check(canon / "toy_engine", snaps) == []


def test_released_version_snapshot_is_not_rewritten(tmp_path, canon):
    snaps = tmp_path / "снимки"
    kcc.write(canon, snaps)
    make_package(tmp_path, "1.0.0", {"status": "что с машиной", "catalog": "каталог"})
    with pytest.raises(kos_release.ReleaseError, match="поднимите версию"):
        kcc.write(canon, snaps)


def test_new_version_reports_difference_with_previous(tmp_path, canon):
    snaps = tmp_path / "снимки"
    kcc.write(canon, snaps)
    make_package(tmp_path, "1.1.0", {"status": "что с машиной — только чтение", "catalog": "каталог"})
    report = kcc.write(canon, snaps)
    assert report[-1] == "разница с 1.0.0: + catalog, ~ status"


def test_release_checked_against_its_own_snapshot_and_passport_hash(tmp_path, canon):
    snaps = tmp_path / "снимки"
    kcc.write(canon, snaps)
    into = tmp_path / "проект" / "toy_engine"
    passport = kos_release.issue(canon, into, first=True)
    assert passport["catalog_sha256"] == kcc.catalog_hash(canon / "toy_engine")
    make_package(tmp_path, "1.1.0", {"status": "другое"})           # канон ушёл вперёд — выпуск сверяется со своим
    commit(tmp_path, "1.1.0")
    assert kcc.check(into, snaps) == []
    data = json.loads((into / kos_release.PASSPORT).read_text(encoding="utf-8"))
    data["catalog_sha256"] = "0" * 64
    (into / kos_release.PASSPORT).write_text(json.dumps(data), encoding="utf-8")
    assert any("паспорт" in p for p in kcc.check(into, snaps))


def test_missing_snapshot_is_a_problem(tmp_path, canon):
    problems = kcc.check(canon / "toy_engine", tmp_path / "пусто")
    assert len(problems) == 2 and "нет снимка 1.0.0.json" in problems[0]
