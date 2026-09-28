"""Подкоманда `shift-share` (задача 7б.8): изменение доли раскладывается на вклад структуры и вклад конверсии.

Читающая: раскладывает уже записанные числа и ничего не пишет. Числа отбираются из хранилища фильтрами — по образцу
команды снимка, а не по номерам: сегментов десятки, перечислять их номера руками нереально.

Правила разложения проверены тестами ядра (`test_goal_noise_shift.py`) и известным ответом инстанса
(`tests/instance/test_shift_share_known_answer.py`): сегменты обоих окон совпадают, числа одного съёма, один кабинет,
поток и источник, тождество «структура + конверсия = изменение» обязано сходиться (страж 4). Здесь — что команда
отбирает нужные числа, зовёт ядро и печатает три строки `render()`.
"""
import json
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

from growth_engine.core.storage import RegistryStore
from growth_engine.registry import SUBCOMMANDS, run
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.helpers import AUG, MAY, n

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = str(FIXTURES / "config_minimal.yaml")
TODAY = date(2026, 9, 15)

# Два окна по двум каналам: доля падает с 15/200 до 14/200, структура и конверсия расходятся в разные стороны.
BEFORE = {"a": (100, 10), "b": (100, 5)}
AFTER = {"a": (50, 5), "b": (150, 9)}


def segment_numbers(data, period):
    base = [replace(n("visits", value, flow="web", period=period), segment=f"channel={key}")
            for key, (value, _) in data.items()]
    events = [replace(n("leads", events_value, flow="web", period=period), segment=f"channel={key}")
              for key, (_, events_value) in data.items()]
    return base + events


def store(folder) -> RegistryStore:
    return RegistryStore(MarkdownBackend(Path(folder)), allowed_domains=(), today=lambda: TODAY)


def with_numbers(folder):
    s = store(folder)
    s.create_numbers(segment_numbers(BEFORE, MAY) + segment_numbers(AFTER, AUG))
    return s


def call(folder, **changes):
    args = Namespace(cmd="shift-share", config=CONFIG, out=str(folder), lark_book=None, dry_run=False, cycle="Ц-1",
                     id=None, status=None, json=None, secrets=None, source=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    return run(args, out=lines.append, today=lambda: TODAY), lines


def windows_json(**changes) -> str:
    fields = {"base_metric": "visits", "events_metric": "leads", "scope": "p1", "flow": "web",
              "windows": ["2026-05", "2026-08"]}
    fields.update(changes)
    return json.dumps(fields, ensure_ascii=False)


# --- разложение ---

def test_shift_share_is_a_reading_subcommand():
    assert SUBCOMMANDS["shift-share"].writes is False


def test_three_numbers_are_printed_as_full_lines(tmp_path):
    """Вклад структуры, вклад конверсии и изменение доли — тремя строками render() со статусом и знаменателем."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=windows_json())
    assert code == 0, lines[-1]
    text = "\n".join(lines)
    assert "вклад структуры" in text and "вклад конверсии" in text and "изменение доли" in text
    assert "сегментов: 2" in text


def test_identity_holds_and_is_stated(tmp_path):
    """Тождество «структура + конверсия = изменение» — то, ради чего разложение и делается: оно названо в выводе."""
    with_numbers(tmp_path)
    _, lines = call(tmp_path, json=windows_json())
    assert any("сходится" in line for line in lines)


def test_nothing_is_written(tmp_path):
    with_numbers(tmp_path)
    before = {p.name: p.read_text(encoding="utf-8") for p in Path(tmp_path).iterdir()}
    call(tmp_path, json=windows_json())
    after = {p.name: p.read_text(encoding="utf-8") for p in Path(tmp_path).iterdir()}
    assert before == after


def test_same_window_twice_is_code_1(tmp_path):
    """Разложение сравнивает два разных окна: одно и то же дважды — страж 4."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=windows_json(windows=["2026-05", "2026-05"]))
    assert code == 1 and "[страж 4]" in lines[-1]


def test_missing_window_numbers_is_code_1(tmp_path):
    """Окна, по которому в хранилище нет чисел, — стоп с названием окна, а не пустое разложение."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=windows_json(windows=["2026-05", "2026-07"]))
    assert code == 1 and "2026-07" in lines[-1]


def test_unknown_metric_is_code_1(tmp_path):
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=windows_json(base_metric="посетители"))
    assert code == 1 and "посетители" in lines[-1]


def test_windows_must_be_two(tmp_path):
    with_numbers(tmp_path)
    code, lines = call(tmp_path, json=windows_json(windows=["2026-05"]))
    assert code == 1 and "два окна" in lines[-1]


def test_json_is_required(tmp_path):
    with_numbers(tmp_path)
    code, lines = call(tmp_path)
    assert code == 1 and "base_metric" in lines[-1]
