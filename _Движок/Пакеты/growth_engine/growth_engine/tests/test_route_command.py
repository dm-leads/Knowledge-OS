"""Команда маршрута (задача 4.7) на фикстурах: печать черновика без записи; любой страж — код 1; согласованный маршрут
пишется в хранилище ключом (5.6)."""
import re
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from growth_engine.route import run

FIXTURES = Path(__file__).parent / "fixtures"


def call(**changes):
    args = Namespace(config=str(FIXTURES / "config_minimal.yaml"), source_map=str(FIXTURES / "source_map_minimal.yaml"),
                     task="объём лидов", cycle="Ц-1", main_class="C", sales_goal="нет")
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    return run(args, out=lines.append, today=lambda: date(2026, 9, 14)), lines


def test_draft_printed_and_nothing_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, lines = call()
    text = "\n".join(lines)
    assert code == 0 and sum(line.startswith("шаг ") for line in lines) == 10
    assert "ожидаемая уверенность вывода: средняя" in text and "маршрут не записан" in text
    assert "цель — продажи: нет" in text
    assert list(tmp_path.iterdir()) == []


# В карте фикстуры нет класса D: шаг 1 получает заменитель из таблицы Р2.
def test_missing_money_class_printed_with_substitute():
    _, lines = call()
    step1 = next(line for line in lines if line.startswith("шаг 1 "))
    assert "D — заменитель" in step1 and "грубый расчёт" in step1


@pytest.mark.parametrize("changes, marker", [(dict(task="рост всего"), "бизнес-задача"),
                                             (dict(main_class="G"), "не из A–F"),
                                             (dict(main_class=None), "--main-class"),
                                             (dict(sales_goal=None), "--sales-goal")])
def test_guard_gives_code_1(changes, marker):
    code, lines = call(**changes)
    assert code == 1 and "[страж 9]" in lines[-1] and marker in lines[-1]


def test_agreed_route_is_written_and_read_back(tmp_path):
    folder = tmp_path / "хранилище"
    code, lines = call(out=str(folder))
    assert code == 0 and lines[-1] == "ИТОГ: маршрут записан и прочитан" and (folder / "Маршрут цикла.md").is_file()
    written = re.search(r"«Маршрут цикла»: записано (\d+), прочитано (\d+)$", lines[-2])
    assert written and written.group(1) == written.group(2) and int(written.group(1)) > 0
    code, lines = call(out=str(folder))
    assert code == 0 and lines[-2].endswith("«Маршрут цикла»: записано 0, прочитано 0")


def test_agreed_route_is_written_to_a_google_book():
    """До 28.09.2026 маршрут сам проверял ключи хранилища по старому списку и не пускал книгу Google; теперь ключ
    хранилища понимает один выбор хранилища."""
    from growth_engine.storage.google_sheets import GoogleBridge
    from growth_engine.tests.fake_google import FakeSpreadsheet
    book = FakeSpreadsheet()
    args = Namespace(config=str(FIXTURES / "config_minimal.yaml"), source_map=str(FIXTURES / "source_map_minimal.yaml"),
                     task="объём лидов", cycle="Ц-1", main_class="C", sales_goal="нет", google_book="книга-проба")
    lines = []
    code = run(args, out=lines.append, today=lambda: date(2026, 9, 14),
               google_factory=lambda: GoogleBridge(open_book=lambda key: book, sleep=lambda s: None))
    assert code == 0 and lines[-1] == "ИТОГ: маршрут записан и прочитан"
    assert any(sheet["title"] == "Маршрут цикла" for sheet in book.sheets.values())
