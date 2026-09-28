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
    (root / "_Движок" / "Шаблоны").mkdir(parents=True)
    (root / "_Движок" / "Шаблоны" / "Шаблон — Журнал действий.md").write_text(
        "# Журнал действий — <проект>, <ГГГГ-ММ>\n\nклассы: <канон>\n", encoding="utf-8")
    (root / "_Движок" / "Шаблоны" / "Шаблон — База инцидентов.md").write_text(
        "# База инцидентов — <проект>\n\nклассы: <канон>\\Методологии\n", encoding="utf-8")
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
    assert "журнал действий: создан" in out and "база инцидентов: создана" in out


JOURNAL = ("0 — System Docs", "Журнал действий")
INCIDENTS = ("0 — System Docs", "Справочник — База инцидентов.md")


def test_new_project_gets_a_journal_and_an_incident_base_from_the_templates(canon, project):
    report = connect(project, canon, today=date(2026, 9, 28))
    month = project.joinpath(*JOURNAL) / "Журнал — 2026-09.md"
    incidents = project.joinpath(*INCIDENTS)
    assert month.read_text(encoding="utf-8").startswith("# Журнал действий — Мой проект, 2026-09")
    assert f"классы: {canon}" in month.read_text(encoding="utf-8")
    assert incidents.read_text(encoding="utf-8").startswith("# База инцидентов — Мой проект")
    assert "<канон>" not in incidents.read_text(encoding="utf-8")
    assert report.journal == "создан" and report.incidents == "создана"


def test_block_names_the_three_files_and_when_to_read_them(canon, project):
    connect(project, canon, today=date(2026, 9, 28))
    inside = block_of((project / "CLAUDE.md").read_text(encoding="utf-8"))
    assert "Журнал действий: `0 — System Docs\\Журнал действий\\`" in inside
    assert "База инцидентов: `0 — System Docs\\Справочник — База инцидентов.md`" in inside
    assert "в начале чата" in inside and "до гипотез" in inside


def test_existing_journal_and_incident_base_are_not_touched(canon, project):
    connect(project, canon, today=date(2026, 9, 28))
    month = project.joinpath(*JOURNAL) / "Журнал — 2026-09.md"
    incidents = project.joinpath(*INCIDENTS)
    month.write_text("мои записи\n", encoding="utf-8")
    incidents.write_text("мои инциденты\n", encoding="utf-8")
    report = connect(project, canon, today=date(2026, 10, 3))
    assert month.read_text(encoding="utf-8") == "мои записи\n"
    assert incidents.read_text(encoding="utf-8") == "мои инциденты\n"
    assert not (project.joinpath(*JOURNAL) / "Журнал — 2026-10.md").exists()
    assert report.journal == "уже есть" and report.incidents == "уже есть"


def test_project_with_its_own_files_points_to_them_and_the_block_remembers(canon, project):
    (project / "Мета" / "Журнал").mkdir(parents=True)
    (project / "Мета" / "Журнал" / "2026-09.md").write_text("старые записи\n", encoding="utf-8")
    (project / "Мета" / "Инциденты.md").write_text("старые инциденты\n", encoding="utf-8")
    connect(project, canon, today=date(2026, 9, 28), journal=project / "Мета" / "Журнал",
            incidents="Мета\\Инциденты.md")
    connect(project, canon, today=date(2026, 9, 28))
    inside = block_of((project / "CLAUDE.md").read_text(encoding="utf-8"))
    assert "Журнал действий: `Мета\\Журнал\\`" in inside and "База инцидентов: `Мета\\Инциденты.md`" in inside
    assert not project.joinpath(*INCIDENTS).exists() and not project.joinpath(*JOURNAL).exists()
    assert (project / "Мета" / "Инциденты.md").read_text(encoding="utf-8") == "старые инциденты\n"


def test_files_outside_the_project_are_kept_by_absolute_path(canon, project, tmp_path):
    shared = tmp_path / "Общий журнал"
    shared.mkdir()
    connect(project, canon, today=date(2026, 9, 28), journal=shared)
    inside = block_of((project / "CLAUDE.md").read_text(encoding="utf-8"))
    assert f"Журнал действий: `{str(shared)}\\`" in inside
    assert (shared / "Журнал — 2026-09.md").is_file() is False  # папка уже была — месяц заводит агент при первой записи


def test_missing_templates_stop_before_anything_is_written(canon, project):
    (canon / "_Движок" / "Шаблоны" / "Шаблон — База инцидентов.md").unlink()
    with pytest.raises(ConnectError, match="Шаблон — База инцидентов"):
        connect(project, canon, today=date(2026, 9, 28))
    assert not (project / "CLAUDE.md").exists()


def test_command_line_accepts_paths_to_existing_files(canon, project):
    (project / "Журнал.md").write_text("записи\n", encoding="utf-8")
    assert kos_connect.main(["--project", str(project), "--canon", str(canon),
                             "--journal", "Журнал.md", "--incidents", "Инциденты.md"]) == 0
    inside = block_of((project / "CLAUDE.md").read_text(encoding="utf-8"))
    assert "Журнал действий: `Журнал.md`" in inside and (project / "Инциденты.md").is_file()


def test_old_block_without_file_lines_and_similar_files_stop_instead_of_a_second_copy(canon, project):
    """Ревью 28.09: блок старой версии без строк журнала + свой журнал проекта — второй экземпляр не заводим."""
    (project / "00 — Мета" / "Журнал действий").mkdir(parents=True)
    (project / "00 — Мета" / "Журнал действий" / "2026-09.md").write_text("записи\n", encoding="utf-8")
    (project / "CLAUDE.md").write_text(f"# П\n\n{BEGIN}\n- канон сверен: 2026-09-20\n{END}\n", encoding="utf-8")
    with pytest.raises(ConnectError, match="--journal"):
        connect(project, canon, today=date(2026, 9, 28), verified=date(2026, 9, 28))
    assert not (project / "0 — System Docs").exists() and "2026-09-20" in (project / "CLAUDE.md").read_text(
        encoding="utf-8")


def test_default_paths_passed_explicitly_are_created_despite_similar_files(canon, project):
    (project / "Разбор инцидента.md").write_text("старый разбор\n", encoding="utf-8")
    report = connect(project, canon, today=date(2026, 9, 28), journal=kos_connect.DEFAULT_JOURNAL,
                     incidents=kos_connect.DEFAULT_INCIDENTS)
    assert report.journal == "создан" and report.incidents == "создана"


@pytest.mark.parametrize("given", ["Инциденты", "Инциденты\\", "папка"])
def test_incident_base_must_be_a_markdown_file(canon, project, given):
    (project / "папка").mkdir()
    with pytest.raises(ConnectError, match="файл .md"):
        connect(project, canon, today=date(2026, 9, 28), incidents=given)


def test_journal_path_with_extension_is_a_file(canon, project):
    report = connect(project, canon, today=date(2026, 9, 28), journal="Журнал.txt")
    assert (project / "Журнал.txt").is_file() and report.journal == "создан"


def test_relative_path_outside_the_project_is_stored_absolute(canon, project, tmp_path):
    (tmp_path / "Общий").mkdir()
    connect(project, canon, today=date(2026, 9, 28), journal="..\\Общий")
    inside = block_of((project / "CLAUDE.md").read_text(encoding="utf-8"))
    assert f"Журнал действий: `{(tmp_path / 'Общий').resolve()}\\`" in inside and "..\\" not in inside


def test_write_error_gives_exit_code_1_and_no_link(canon, project, capsys):
    (project / "Файл").write_text("x\n", encoding="utf-8")
    code = kos_connect.main(["--project", str(project), "--canon", str(canon), "--journal", "Файл\\Журнал\\",
                             "--rules-import"])
    assert code == 1 and "❌" in capsys.readouterr().out
    assert not (project / ".kos").exists() and not (project / "CLAUDE.md").exists()
