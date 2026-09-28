"""Скиллы шагов цикла (задача 7.2): шапка и триггеры, запреты, отсутствие имён компании и сервисов, существование
упомянутых команд; установка — ссылкой на исходник в каноне Knowledge-OS (с 28.09.2026), а не копией.

Исходник навыков — `D:\\Knowledge-OS\\_Движок\\Исходники скиллов\\growth-*` (корень — переменная KNOWLEDGE_OS): один
на устройство, в проектах и в глобальной папке — ссылки. Копия застывала в день копирования, и правка до других
проектов не доходила. Тексты навыков проверяются там, где они живут, — в каноне.
"""
import os
import re
from argparse import Namespace
from pathlib import Path

import pytest

from growth_engine.install_skills import SOURCE, install, is_link_to, run, skill_dirs, verify

ENGINE = Path(__file__).resolve().parents[1]
MODULES = {path.stem for path in ENGINE.glob("*.py")} - {"__init__"}
EXPECTED = {"growth-step0-sources", "growth-step1-goal", "growth-route", "growth-diagnose",
            "growth-hypotheses", "growth-close-cycle"}
# Тот же список, что у границы ядра: скилл переносится в другую компанию без правок.
FORBIDDEN = ("roistat", "lark", "amocrm", "amo_", "moysklad", "мойсклад", "yandex", "яндекс", "metrika",
             "custom_", "breezeks", "бризекс", "atmeex", "55101", "234577", "sipuni", "wazzup")
BANS = ("деньги", "персональные данные", "нет данных")
COMMAND = re.compile(r"growth_engine\.([a-z_]+)")


def skills():
    return sorted(path for path in SOURCE.glob("growth-*/SKILL.md"))


def head(text: str) -> dict:
    block = text.split("---", 2)[1]
    return {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
            for line in block.splitlines() if ":" in line and not line.startswith(" ")}


def test_every_planned_skill_exists():
    assert {path.parent.name for path in skills()} == EXPECTED, f"исходник навыков в каноне не найден: {SOURCE}"


def test_no_second_copy_of_the_skills_in_the_engine():
    """Второй исходник в репозитории инстанса снова разведёт копии — навыки живут только в каноне."""
    assert not (ENGINE / "skills").exists()


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_header_has_name_and_russian_triggers(path):
    text = path.read_text(encoding="utf-8")
    fields = head(text)
    assert fields.get("name") == path.parent.name and text.startswith("---\n")
    description = text.split("description:", 1)[1].split("disable-model-invocation", 1)[0]
    assert "Триггеры:" in description and description.count("«") >= 4


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_no_company_or_service_names(path):
    text = path.read_text(encoding="utf-8").lower()
    assert [word for word in FORBIDDEN if word in text] == []


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_bans_are_written_down(path):
    text = path.read_text(encoding="utf-8").lower()
    assert all(ban in text for ban in BANS) and "статус" in text


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_every_mentioned_command_exists(path):
    mentioned = set(COMMAND.findall(path.read_text(encoding="utf-8")))
    assert mentioned and mentioned <= MODULES, f"нет таких команд: {sorted(mentioned - MODULES)}"


# --- установка: ссылкой на исходник в каноне ---
# Установка проверяется на своём маленьком исходнике: живой канон здесь только читается, а ссылки ставятся во
# временные папки — тест не должен трогать папки навыков устройства.

def fake_source(tmp_path) -> Path:
    source = tmp_path / "Исходники скиллов"
    for name in ("growth-a", "growth-b", "kos-чужой"):
        (source / name).mkdir(parents=True)
        (source / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return source


def call(tmp_path, source, *, dry_run=False, replace_copies=False):
    lines = []
    args = Namespace(project=str(tmp_path / "проект" / "skills"), **{"global": str(tmp_path / "глобальная" / "skills")},
                     dry_run=dry_run, replace_copies=replace_copies)
    return run(args, out=lines.append, folder=source), lines


def test_links_go_to_both_places_and_only_engine_skills_are_linked(tmp_path):
    source = fake_source(tmp_path)
    code, lines = call(tmp_path, source)
    project, glob = tmp_path / "проект" / "skills", tmp_path / "глобальная" / "skills"
    assert code == 0 and lines[-1].startswith("ИТОГ: ссылки на месте")
    for target in (project, glob):
        assert is_link_to(target / "growth-a", source / "growth-a") and is_link_to(target / "growth-b", source / "growth-b")
        assert not (target / "kos-чужой").exists()
    assert verify(source, [project, glob]) == []


def test_edit_in_the_source_is_seen_through_the_link(tmp_path):
    """Ради этого всё и делается: правка в каноне видна везде без переустановки."""
    source = fake_source(tmp_path)
    call(tmp_path, source)
    (source / "growth-a" / "SKILL.md").write_text("---\nname: growth-a\n---\nновое правило\n", encoding="utf-8")
    seen = (tmp_path / "глобальная" / "skills" / "growth-a" / "SKILL.md").read_text(encoding="utf-8")
    assert "новое правило" in seen


def test_repeat_changes_nothing(tmp_path):
    source = fake_source(tmp_path)
    call(tmp_path, source)
    code, lines = call(tmp_path, source)
    assert code == 0 and "поставлено 0" in lines[-1] and "уже на месте 4" in lines[-1]


def test_dry_run_changes_nothing(tmp_path):
    source = fake_source(tmp_path)
    code, lines = call(tmp_path, source, dry_run=True)
    assert code == 0 and lines[-1] == "сухой прогон: ничего не изменено"
    assert not (tmp_path / "проект").exists() and not (tmp_path / "глобальная").exists()


def test_copy_in_place_is_reported_and_left_untouched(tmp_path):
    source = fake_source(tmp_path)
    copy = tmp_path / "глобальная" / "skills" / "growth-a"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text("старая копия", encoding="utf-8")
    code, lines = call(tmp_path, source)
    assert code == 1 and any("копия" in line and "--replace-copies" in line for line in lines)
    assert (copy / "SKILL.md").read_text(encoding="utf-8") == "старая копия" and not is_link_to(copy, source / "growth-a")


def test_copy_is_moved_aside_next_to_the_skills_folder_and_replaced_by_a_link(tmp_path):
    """Копия уходит в резерв РЯДОМ с папкой навыков, а не внутрь: иначе её SKILL.md с тем же именем виден дважды.
    Удаляется резерв только после проверки навыка в новой сессии — это делает человек, не установщик."""
    source = fake_source(tmp_path)
    skills_dir = tmp_path / "глобальная" / "skills"
    (skills_dir / "growth-a").mkdir(parents=True)
    (skills_dir / "growth-a" / "SKILL.md").write_text("старая копия", encoding="utf-8")
    code, lines = call(tmp_path, source, replace_copies=True)
    reserves = [path for path in skills_dir.parent.iterdir() if path.name.startswith("skills-резерв-")]
    assert code == 0 and len(reserves) == 1
    assert (reserves[0] / "growth-a" / "SKILL.md").read_text(encoding="utf-8") == "старая копия"
    assert is_link_to(skills_dir / "growth-a", source / "growth-a")
    assert not any(path.name.startswith("skills-резерв-") for path in skills_dir.iterdir())


def test_foreign_link_is_not_touched(tmp_path):
    source = fake_source(tmp_path)
    elsewhere = tmp_path / "другой исходник" / "growth-a"
    elsewhere.mkdir(parents=True)
    skills_dir = tmp_path / "глобальная" / "skills"
    skills_dir.mkdir(parents=True)
    from growth_engine.install_skills import link
    link(skills_dir / "growth-a", elsewhere)
    code, lines = call(tmp_path, source, replace_copies=True)
    assert code == 1 and any("ссылка ведёт не туда" in line for line in lines)
    assert is_link_to(skills_dir / "growth-a", elsewhere)


def test_missing_source_folder_is_code_1(tmp_path):
    code, lines = call(tmp_path, tmp_path / "нет папки")
    assert code == 1 and "исходника навыков нет" in lines[-1]


def test_source_root_follows_the_environment(tmp_path, monkeypatch):
    """Канон на другой машине может лежать не на D: — корень задаётся переменной KNOWLEDGE_OS."""
    from growth_engine.install_skills import source_root
    monkeypatch.setenv("KNOWLEDGE_OS", str(tmp_path))
    assert source_root() == tmp_path / "_Движок" / "Исходники скиллов"
    monkeypatch.delenv("KNOWLEDGE_OS")
    assert source_root() == Path("D:/Knowledge-OS") / "_Движок" / "Исходники скиллов"


# --- правки по ревью этапа 7а ---

REQUIRED = re.compile(r'add_argument\("--([a-z-]+)"[^)]*required=True')
CALL = re.compile(r"py -3 -m growth_engine\.([a-z_]+)((?:[^`\n]|\n   )*)")


def required_keys(module: str) -> set:
    """Обязательные ключи команды — из её исходника, без запуска самой команды."""
    return set(REQUIRED.findall((ENGINE / f"{module}.py").read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_command_lines_carry_every_required_key(path):
    text = path.read_text(encoding="utf-8")
    calls = CALL.findall(text)
    assert calls, "в скилле нет ни одной команды движка"
    for module, tail in calls:
        given = set(re.findall(r"--([a-z-]+)", tail))
        missing = required_keys(module) - given
        assert not missing, f"{path.parent.name}: у команды {module} не хватает ключей {sorted(missing)}"


def test_install_reports_unavailable_target(tmp_path):
    source = fake_source(tmp_path)
    blocker = tmp_path / "занято"
    blocker.write_text("файл вместо папки", encoding="utf-8")
    lines = []
    code = run(Namespace(project=str(tmp_path / "проект"), **{"global": str(blocker)}, dry_run=False,
                         replace_copies=False), out=lines.append, folder=source)
    assert code == 1 and lines[-1].startswith("ИТОГ: ссылки НЕ подтверждены")


# --- правки по состязательной проверке Codex (С7, 15.09.2026) ---
# Четыре находки второго агента, принятые после разбора: скилл требовал числа только из строк команд, но сам отсылал
# «за целью в конфигурацию» без способа её привести; опирался на строку гейта, не запуская гейт; объявлял коды
# возврата словом, которого нет ни в одном журнале; допускал производные счётчики («шесть классов» вместо
# перечисления). Каждая проверка смотрит текст скилла, а не запускает команду.

GOAL_KEYS = ("metric", "window_months", "baseline", "target_periods")


def test_step1_names_goal_keys_and_asks_to_quote_them():
    """Цель — не «из конфигурации» вообще, а названные ключи, перенесённые цитатой."""
    text = (SOURCE / "growth-step1-goal" / "SKILL.md").read_text(encoding="utf-8")
    missing = [key for key in GOAL_KEYS if key not in text]
    assert not missing, f"шаг 1 не называет ключи цели: {missing}"
    assert "цитат" in text.lower(), "шаг 1 не требует приводить цель цитатой строк конфигурации"


def test_step1_runs_the_gate_itself_or_names_its_run():
    """Свежесть снимка решается строкой гейта — значит, гейт либо запускается здесь, либо назван прогон, откуда строка."""
    text = (SOURCE / "growth-step1-goal" / "SKILL.md").read_text(encoding="utf-8")
    assert "growth_engine.health" in text, "шаг 1 опирается на строку гейта, не давая команды гейта"


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_return_code_is_proved_by_a_line(path):
    """Код возврата не печатает ни одна команда: скилл обязан сохранить его строкой, иначе это слово агента."""
    text = path.read_text(encoding="utf-8")
    if "py -3 -m growth_engine." not in text:
        pytest.skip("в скилле нет команд движка")
    assert "КОД ВОЗВРАТА" in text, f"{path.parent.name}: код возврата ничем не подтверждается"


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_derived_counters_are_forbidden(path):
    """«Шесть классов» вместо перечисления A–F — своё число: перечисление переносится как есть."""
    text = path.read_text(encoding="utf-8").lower()
    assert "не считать" in text or "не пересчитывать" in text
    assert "перечислен" in text, f"{path.parent.name}: нет запрета на производные счётчики"
