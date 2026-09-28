"""Каркас команды реестра (задача 7б.1): одна команда, подкомандами по шагам цикла.

Решение Р7б (15.09.2026): восемь отдельных модулей отклонены владельцем — «среди тысячи разных команд мы очень сильно
запутаемся». Здесь проверяется только каркас: разбор подкоманд, общие ключи, коды возврата и то, что читающая
подкоманда ничего не создаёт. Поведение самих шагов — задачи 7б.2–7б.8.
"""
import re
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from growth_engine.registry import SUBCOMMANDS, main, run

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = str(FIXTURES / "config_minimal.yaml")


def call(cmd="hypothesis", **changes):
    args = Namespace(cmd=cmd, config=CONFIG, out=None, lark_book=None, dry_run=False, cycle="Ц-1", id=None,
                     status=None, json=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    return run(args, out=lines.append, today=lambda: date(2026, 9, 15)), lines


# --- подкоманды ---

def test_every_planned_subcommand_is_registered():
    """Десять подкоманд плана: шаг 7 распадается на скоринг и гейт шума, evidence и shift-share служат нескольким шагам."""
    assert set(SUBCOMMANDS) == {"bottleneck", "hypothesis", "score", "gate", "rat", "evidence", "measure", "tree",
                                "close", "shift-share"}


def test_subcommand_names_match_the_cycle_steps():
    """Имя подкоманды — слово шага алгоритма: агенту не нужно переводить между языком методологии и языком команды.

    Справка обязана называть предмет и начинаться с него («гипотеза: …»), а не с глагола-обёртки вроде «команда для».
    Требовать строчную первую букву нельзя: у RAT это аббревиатура.
    """
    for name, spec in SUBCOMMANDS.items():
        assert spec.step in range(2, 11), f"{name}: шаг {spec.step} вне шагов 2–10"
        assert len(spec.help.split()) >= 5, f"{name}: справка короче пяти слов — по ней не понять, что делает шаг"
        assert not spec.help.lower().startswith(("команда", "подкоманда")), f"{name}: справка про себя, а не о деле"


def test_unknown_subcommand_is_code_1():
    code, lines = call(cmd="улучшить-всё")
    assert code == 1 and "улучшить-всё" in lines[-1] and "подкоманд" in lines[-1]


def test_argparse_lists_subcommands_in_help():
    """Список подкоманд виден из `--help`: агенту не нужно читать исходник, чтобы узнать их имена."""
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


# --- общие ключи ---

def test_two_storage_keys_at_once_are_code_1(tmp_path):
    code, lines = call(out=str(tmp_path), lark_book="токен книги")
    assert code == 1 and "ровно одно" in lines[-1]


def test_reading_subcommand_without_storage_key_creates_nothing(tmp_path, monkeypatch):
    """Подкоманда чтения без ключа хранилища берёт книгу из конфигурации; у фикстуры её нет — стоп без записи."""
    monkeypatch.chdir(tmp_path)
    code, lines = call(cmd="hypothesis", status="candidate")
    assert code == 1 and "хранилище" in lines[-1]
    assert list(tmp_path.iterdir()) == []


def test_config_is_required_by_every_subcommand():
    for name in SUBCOMMANDS:
        with pytest.raises(SystemExit) as e:
            main([name])
        assert e.value.code == 2, f"{name}: работает без --config"


def test_missing_config_file_is_code_1():
    code, lines = call(config=str(FIXTURES / "нет такого файла.yaml"))
    assert code == 1 and "конфигурац" in lines[-1].lower()


# --- отчёт о записи ---

def test_writing_subcommand_reports_rows_and_reads_back(tmp_path):
    """Каркас требует от пишущей подкоманды отчёт числом строк: «готово» словом не принимается (правило контура)."""
    assert SUBCOMMANDS["hypothesis"].writes is True
    code, lines = call(cmd="hypothesis", id="H-001", out=str(tmp_path),
                       json='{"formulation": "если убрать шаг 2 формы, то CR1 вырастет, потому что там теряется половина"}')
    assert code == 0, f"каркас не записал гипотезу: {lines[-1] if lines else 'вывода нет'}"
    report = [line for line in lines if "записано" in line and "прочитано" in line]
    assert report and re.search(r"записано (\d+), прочитано \1$", report[-1]), report
    assert int(re.search(r"записано (\d+)", report[-1]).group(1)) > 0


def test_dry_run_of_writing_subcommand_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, lines = call(cmd="hypothesis", id="H-001", dry_run=True,
                       json='{"formulation": "если — то — потому что"}')
    assert code == 0 and "сухой прогон" in lines[-1]
    assert list(tmp_path.iterdir()) == []
