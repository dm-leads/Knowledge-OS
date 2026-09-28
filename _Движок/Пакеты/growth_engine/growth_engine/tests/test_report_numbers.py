"""Сверка чисел отчёта с выводом команд (задача 7.3): разделители тысяч и запятая, даты и номера стражей числами не
считаются, число без источника — код 1."""
from argparse import Namespace
from pathlib import Path

import pytest

from growth_engine.report_numbers import canonical, check, numbers, run

# Строки такого вида печатают команды движка: render() и гейт.
SNAPSHOT = ("   leads = 1 578 · кабинет p1 · период 01.08.2026–31.08.2026 · знаменатель — · "
            "источник C:analytics-x:data · снято 03.09.2026 · статус факт")
GATE = ("✅ [страж 2] известные ответы: ad_spend · кабинет brz · окно 01.06.2026–30.06.2026 · съём 15.09.2026: "
        "127 842 против известного 127 842 ₽: разница +0.11 (0,0%), в допуске (допуск 1 ₽ или 0,0%)")


def call(tmp_path, report: str, logs=(SNAPSHOT,), missing_log=False):
    report_path = tmp_path / "отчёт.md"
    report_path.write_text(report, encoding="utf-8")
    names = []
    for index, text in enumerate(logs):
        path = tmp_path / f"журнал{index}.txt"
        path.write_text(text, encoding="utf-8")
        names.append(str(path))
    if missing_log:
        names.append(str(tmp_path / "нет такого файла.txt"))
    lines = []
    code = run(Namespace(report=str(report_path), log=names), out=lines.append)
    return code, lines


# --- разбор чисел ---

@pytest.mark.parametrize("text, expected", [("1 578", "1578"), ("127 842", "127842"), ("0,0", "0"),
                                            ("+0.11", "0.11"), ("35", "35"), ("14 208,50", "14208.5")])
def test_canonical_form_of_a_number(text, expected):
    assert numbers(text) == [expected]


def test_dates_guards_steps_and_artifact_ids_are_not_numbers():
    text = "шаг 2, [страж 13], 01.08.2026, 2026-09-14, 2026-09, v1.0, D-v1-H1, H-001"
    assert numbers(text) == []


def test_numbers_of_a_render_line():
    assert numbers(SNAPSHOT) == ["1578"]


def test_check_finds_only_what_is_absent():
    assert check("CR1 = 1 578 и 999", SNAPSHOT) == ["999"]
    assert check("1578 и 1 578", SNAPSHOT) == []


# --- команда ---

def test_report_backed_by_command_output_passes(tmp_path):
    code, lines = call(tmp_path, "Заявок веб-потока 1 578 (статус факт), расход 127 842 ₽.", logs=(SNAPSHOT, GATE))
    assert code == 0 and lines[-1].startswith("ИТОГ: каждое число отчёта найдено")
    assert "чисел в отчёте 2, журналов прогона 2" in lines[0]


def test_number_without_a_source_line_is_code_1(tmp_path):
    code, lines = call(tmp_path, "Заявок 1 578, конверсия выросла на 12 процентов.")
    assert code == 1 and lines[-1] == "ИТОГ: чисел без источника 1 — отчёт не принят"
    assert any(line.strip() == "❌ нет в выводе команд: 12" for line in lines)


def test_missing_report_or_log_is_code_1(tmp_path):
    code, lines = call(tmp_path, "1 578", missing_log=True)
    assert code == 1 and "нет журналов прогона" in lines[-1]
    lines = []
    code = run(Namespace(report=str(tmp_path / "нет.md"), log=[]), out=lines.append)
    assert code == 1 and "отчёта нет" in lines[-1]


# --- правки по ревью этапа 7а ---

@pytest.mark.parametrize("text, expected", [("-500", "-500"), ("\u2212500", "-500"), ("1\u202f234", "1234"),
                                            ("1\u2009234", "1234"), ("3,83%", "3.83"), ("-0", "0")])
def test_sign_and_thin_separators_are_understood(text, expected):
    assert numbers(text) == [expected]


def test_minus_is_not_the_same_number_as_plus():
    assert check("сдвиг -500", "сдвиг 500") == ["-500"]


def test_thin_separator_does_not_split_an_invented_number():
    assert check("1\u202f234 заявки", "было 1 и 234") == ["1234"]


def test_percent_value_is_checked():
    assert check("конверсия 3,83%", "cr1 = 3,83% · статус факт") == []
    assert check("конверсия 3,83%", "cr1 = 1,50% · статус факт") == ["3.83"]


def test_dates_stay_out_after_the_sign_fix():
    assert numbers("снято 2026-09-14 · период 01.08.2026–31.08.2026 · окно 2026-09") == []


# Приёмочный прогон 15.09.2026: сверка трижды отклоняла честный отчёт из-за имён журналов и путей — это ярлыки,
# а не значения, и сторож не должен заставлять переименовывать файлы прогона.
@pytest.mark.parametrize("text", ["журналы 01_health.txt и 02_probes.txt", "папка 01 — Проекты/AI Digital Marketing",
                                  "правило §7.1", "C:/путь/stage7/acceptance/04_snapshot.txt"])
def test_file_names_paths_and_section_links_are_not_numbers(text):
    assert numbers(text) == []


def test_real_numbers_next_to_labels_survive():
    assert numbers("журнал 04_snapshot.txt: ampu = 14 208, среднее окна 665") == ["14208", "665"]


# Повторный приёмочный прогон 15.09.2026: скиллы требуют приводить команды дословно, а «py -3» разбиралось как число
# −3 — сверка падала на любом честном отчёте.
@pytest.mark.parametrize("text", ["py -3 -m growth_engine.health --config x",
                                  "py -3 -m growth_engine.cohort --horizons 30,60,90,180,365 --out файл.md",
                                  "ключ --months 3", "py -3 -m growth_engine.economy_run --months 3 --dry-run"])
def test_command_line_keys_are_not_numbers(text):
    assert numbers(text) == []


def test_real_negative_number_still_counts():
    assert numbers("сдвиг -500 сделок") == ["-500"]
    assert check("сдвиг -500", "сдвиг 500") == ["-500"]


def test_report_with_command_lines_passes(tmp_path):
    report = tmp_path / "отчёт.md"
    report.write_text("Запускал `py -3 -m growth_engine.health --config x`; гейт: пройдено 25.", encoding="utf-8")
    log = tmp_path / "журнал.txt"
    log.write_text("ИТОГ: гейт зелёный — пройдено 25, структура 0", encoding="utf-8")
    lines = []
    assert run(Namespace(report=str(report), log=[str(log)]), out=lines.append) == 0
    assert lines[-1].startswith("ИТОГ: каждое число отчёта найдено")
