"""Подключение проекта к канону (ADR-0004, 28.09.2026): блок в CLAUDE.md, ссылка `.kos`, исключение из git.

Каждый тест строит свой маленький канон и папку проекта во временном каталоге.
"""
import os
import subprocess
from datetime import date

import pytest

import kos_connect
from kos_connect import BEGIN, END, ConnectError, connect


@pytest.fixture(autouse=True)
def no_global_import(tmp_path, monkeypatch):
    """Тесты не зависят от настоящего ~/.claude/CLAUDE.md устройства: по умолчанию глобального импорта нет."""
    monkeypatch.setenv("KOS_GLOBAL_PASSPORT", str(tmp_path / "нет-глобального.md"))


@pytest.fixture
def canon(tmp_path):
    root = tmp_path / "Канон"
    (root / "_Движок" / "Подключение").mkdir(parents=True)
    (root / "_Движок" / "Подключение" / "agent-rules.md").write_text("# Правила агента\n", encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "Мой проект"
    folder.mkdir()
    return folder


def block_of(text: str) -> str:
    return text[text.index(BEGIN):text.index(END) + len(END)]


def test_new_project_gets_a_passport_with_the_block_and_the_link(canon, project):
    report = connect(project, canon, today=date(2026, 9, 28))
    text = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert BEGIN in text and END in text and "@.kos/agent-rules.md" in text
    assert "канон сверен: 2026-09-28" in text
    assert os.path.isjunction(project / ".kos")
    assert (project / ".kos" / "agent-rules.md").read_text(encoding="utf-8") == "# Правила агента\n"
    assert report.block_lines > 0 and report.link == "создана"


def test_existing_passport_keeps_its_own_text(canon, project):
    (project / "CLAUDE.md").write_text("# Проект\n\nСвоё правило проекта.\n", encoding="utf-8")
    connect(project, canon, today=date(2026, 9, 28))
    text = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# Проект\n\nСвоё правило проекта.\n") and text.count(BEGIN) == 1


def test_second_run_changes_nothing_and_keeps_the_verified_date(canon, project):
    connect(project, canon, today=date(2026, 9, 28))
    before = (project / "CLAUDE.md").read_text(encoding="utf-8")
    report = connect(project, canon, today=date(2026, 10, 3))
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == before and report.link == "уже есть"


def test_verified_date_is_updated_on_request(canon, project):
    connect(project, canon, today=date(2026, 9, 28))
    connect(project, canon, today=date(2026, 10, 3), verified=date(2026, 10, 3))
    text = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert "канон сверен: 2026-10-03" in text and "канон сверен: 2026-09-28" not in text


def test_hand_edit_inside_the_block_is_replaced_and_outside_is_kept(canon, project):
    connect(project, canon, today=date(2026, 9, 28))
    path = project / "CLAUDE.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("## Канон Knowledge-OS", "## Канон Knowledge-OS\nправка руками") + "\nХвост.\n",
                    encoding="utf-8")
    connect(project, canon, today=date(2026, 9, 28))
    text = path.read_text(encoding="utf-8")
    assert "правка руками" not in text and text.endswith("Хвост.\n") and text.count(BEGIN) == 1


def test_link_is_excluded_from_git_locally_once(canon, project):
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    connect(project, canon, today=date(2026, 9, 28))
    connect(project, canon, today=date(2026, 9, 28))
    exclude = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.count("/.kos/") == 1
    status = subprocess.run(["git", "-C", str(project), "status", "--porcelain"], capture_output=True, text=True,
                            encoding="utf-8").stdout
    assert ".kos" not in status


def test_project_inside_a_repository_is_excluded_by_its_relative_path(canon, tmp_path):
    repo = tmp_path / "репо"
    project = repo / "Проекты" / "Один"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    connect(project, canon, today=date(2026, 9, 28))
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/Проекты/Один/.kos/" in exclude


def test_without_rules_import_there_is_no_link_and_no_import(canon, project):
    connect(project, canon, today=date(2026, 9, 28), rules_import=False)
    text = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@.kos/" not in text and not (project / ".kos").exists()


def test_ordinary_folder_named_kos_is_not_replaced(canon, project):
    (project / ".kos").mkdir()
    (project / ".kos" / "моё.md").write_text("не трогать\n", encoding="utf-8")
    with pytest.raises(ConnectError, match="не ссылка"):
        connect(project, canon, today=date(2026, 9, 28))
    assert (project / ".kos" / "моё.md").is_file()


def test_missing_canon_rules_stop(tmp_path, project):
    with pytest.raises(ConnectError, match="agent-rules.md"):
        connect(project, tmp_path / "нет канона", today=date(2026, 9, 28))


def test_rules_already_imported_globally_are_not_imported_twice(canon, project, tmp_path):
    """С 28.09.2026 ~/.claude/CLAUDE.md сам импортирует правила канона — в проекте второй импорт лишний."""
    global_passport = tmp_path / "global.md"
    global_passport.write_text("# устройство\n\n@kos/agent-rules.md\n", encoding="utf-8")
    report = connect(project, canon, today=date(2026, 9, 28), global_passport=global_passport)
    text = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@.kos/" not in text and not (project / ".kos").exists() and report.link == "не нужна"
    assert "грузятся глобально" in text


def test_without_global_import_the_project_imports_the_rules(canon, project, tmp_path):
    global_passport = tmp_path / "global.md"
    global_passport.write_text("# устройство без импорта\n", encoding="utf-8")
    connect(project, canon, today=date(2026, 9, 28), global_passport=global_passport)
    assert "@.kos/agent-rules.md" in (project / "CLAUDE.md").read_text(encoding="utf-8")


def test_command_line_prints_the_outcome(canon, project, capsys):
    assert kos_connect.main(["--project", str(project), "--canon", str(canon)]) == 0
    out = capsys.readouterr().out
    assert "ИТОГ" in out and "ссылка .kos: создана" in out
