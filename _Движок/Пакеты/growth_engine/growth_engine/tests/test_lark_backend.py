"""Lark-бэкенд хранилища (задача 5.5): мост как процесс и пять примитивов на модели моста — буквы колонок, рост листа,
колонки людей, лимиты сервера, отказ токена, экранирование ячеек."""
import sys
from dataclasses import replace

import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.storage import RegistryStore
from growth_engine.storage.lark_sheets import LarkBackend, LarkBridge, bridge_error, column_letter
from growth_engine.tests.fake_lark import FakeLarkBridge
from growth_engine.tests.helpers import candidate


def backend():
    bridge = FakeLarkBridge()
    return LarkBackend("книга-проба", bridge), bridge


def writes(bridge):
    return [ranges for command, ranges in bridge.calls if command == "write"]


@pytest.mark.parametrize("index, letters", [(1, "A"), (20, "T"), (26, "Z"), (27, "AA"), (52, "AZ"), (53, "BA")])
def test_column_letters(index, letters):
    assert column_letter(index) == letters


# --- рост листа ---

def test_wide_sheet_gets_columns_and_keeps_last_column_empty():
    lark, bridge = backend()
    columns = [f"поле_{i}" for i in range(45)]
    lark.create_sheet("Реестр гипотез", columns)
    grid = bridge.sheets["s1"]["grid"]
    assert lark.header("Реестр гипотез") == columns and len(grid[0]) > 45 and grid[0][-1] is None


def test_writing_past_the_sheet_grows_it_and_keeps_last_row_empty():
    lark, bridge = backend()
    lark.create_sheet("Модель — снимки", ["id", "value"])
    lark.write_rows("Модель — снимки", [(None, {"id": str(i), "value": f"{i}.0"}) for i in range(250)])
    lark.write_rows("Модель — снимки", [(None, {"id": "250", "value": "250.0"})])
    grid = bridge.sheets["s1"]["grid"]
    rows = lark.read_rows("Модель — снимки")
    assert len(rows) == 251 and rows[249] == {"id": "249", "value": "249.0"} and rows[250]["id"] == "250"
    assert len(grid) > 252 and all(cell is None for cell in grid[-1])


# --- колонки людей ---

def test_human_column_between_engine_columns_is_not_touched():
    lark, bridge = backend()
    lark.create_sheet("Карта знаний", ["id", "statement", "комментарий", "verdict"])
    lark.write_rows("Карта знаний", [(None, {"id": "K-001", "statement": "шаг 2 не барьер", "verdict": "открыто"})])
    lark.write_rows("Карта знаний", [(0, {"комментарий": "проверить в октябре"})])
    lark.write_rows("Карта знаний", [(0, {"statement": "шаг 2 — барьер", "verdict": "подтверждено"})])
    assert lark.read_rows("Карта знаний") == [{"id": "K-001", "statement": "шаг 2 — барьер",
                                               "комментарий": "проверить в октябре", "verdict": "подтверждено"}]
    assert writes(bridge)[-1] == 2


def test_unnamed_column_with_human_data_is_not_claimed():
    lark, bridge = backend()
    lark.create_sheet("Карта знаний", ["id", "statement"])
    bridge.sheets["s1"]["grid"][1][2] = "заметка без заголовка"
    with pytest.raises(GuardViolation, match="без имени C") as e:
        lark.add_columns("Карта знаний", ["verdict"])
    assert e.value.guard == 13 and lark.header("Карта знаний") == ["id", "statement"]


def test_update_of_absent_row_position_stops():
    lark, _ = backend()
    lark.create_sheet("лист", ["id"])
    with pytest.raises(GuardViolation) as e:
        lark.write_rows("лист", [(3, {"id": "x"})])
    assert e.value.guard == 13 and lark.read_rows("лист") == []


# --- лимиты сервера ---

def test_consecutive_rows_are_written_as_one_range():
    lark, bridge = backend()
    lark.create_sheet("лист", ["id", "value"])
    lark.write_rows("лист", [(None, {"id": str(i), "value": "v"}) for i in range(85)])
    assert writes(bridge)[-1] == 1


def test_scattered_updates_are_split_by_twenty_ranges():
    lark, bridge = backend()
    lark.create_sheet("лист", ["a", "человек", "b"])
    lark.write_rows("лист", [(None, {"a": f"x{i}", "b": f"y{i}"}) for i in range(60)])
    lark.write_rows("лист", [(p, {"a": "u", "b": "v"}) for p in range(0, 60, 2)])
    rows = lark.read_rows("лист")
    assert writes(bridge)[-4:] == [2, 20, 20, 20]
    assert rows[0] == {"a": "u", "человек": "", "b": "v"} and rows[1] == {"a": "x1", "человек": "", "b": "y1"}


def test_large_write_is_split_by_twenty_thousand_cells():
    lark, bridge = backend()
    lark.create_sheet("лист", ["id", "value"])
    lark.write_rows("лист", [(None, {"id": str(i), "value": "v"}) for i in range(12_000)])
    rows = lark.read_rows("лист")
    assert writes(bridge)[-2:] == [1, 1] and len(rows) == 12_000 and rows[-1]["id"] == "11999"


# --- ячейки и отказ токена ---

def test_raw_formula_text_would_be_lost_but_contract_escapes_it():
    lark, _ = backend()
    lark.create_sheet("лист", ["id", "текст"])
    lark.write_rows("лист", [(None, {"id": "1", "текст": "=1+1"})])
    assert lark.read_rows("лист")[0]["текст"] == "ВЫЧИСЛЕНО"
    store = RegistryStore(backend()[0])
    tricky = replace(candidate(), mechanic="=вычитание шага", owner="true")
    store.create(tricky)
    assert store.read("hypotheses") == [tricky]


def test_expired_token_is_access_error_not_empty():
    lark, bridge = backend()
    bridge.fail_with = "UserAccessToken is invalid or expired (code=99991668; msg=Invalid access token for authorization)"
    with pytest.raises(GuardViolation) as e:
        lark.header("Реестр гипотез")
    assert e.value.guard == 13 and e.value.severity == GuardViolation.COVERAGE and "авторизац" in str(e.value)


def test_other_bridge_errors_are_structure_errors():
    error = bridge_error("Request failed with status code 400")
    assert error.guard == 13 and error.severity == GuardViolation.STRUCTURE


# --- мост как процесс: вместо node — заглушка на Python ---

ECHO = """
import json, sys
while True:
    line = sys.stdin.readline()
    if not line:
        break
    request = json.loads(line)
    if request["command"] == "tabs":
        reply = {"id": request["id"], "ok": True, "result": {"tabs": []}}
    else:
        reply = {"id": request["id"], "ok": False, "error": "UserAccessToken is invalid or expired"}
    print(json.dumps(reply), flush=True)
"""
DEAD = "import sys\nsys.stderr.write('boom: ' + 'A1b2C3d4' * 5 + ' user@example.com\\n')\nsys.exit(3)\n"
SILENT = "import time\ntime.sleep(30)\n"


def bridge_on(tmp_path, code):
    script = tmp_path / "bridge.py"
    script.write_text(code, encoding="utf-8")
    return LarkBridge(script=script, node=sys.executable, timeout=3)


def test_bridge_process_answers_and_maps_access_errors(tmp_path):
    with bridge_on(tmp_path, ECHO) as bridge:
        assert bridge.call("tabs", book="x") == {"tabs": []}
        with pytest.raises(GuardViolation) as e:
            bridge.call("read", book="x", ranges=["s1!A1:A1"])
    assert e.value.severity == GuardViolation.COVERAGE


def test_dead_bridge_reports_its_output_without_secrets(tmp_path):
    with bridge_on(tmp_path, DEAD) as bridge:
        with pytest.raises(GuardViolation) as e:
            bridge.call("tabs", book="x")
    message = str(e.value)
    assert "boom" in message and "A1b2C3d4" not in message and "example.com" not in message
    assert e.value.severity == GuardViolation.COVERAGE


def test_silent_bridge_is_stopped_by_timeout(tmp_path):
    with bridge_on(tmp_path, SILENT) as bridge:
        with pytest.raises(GuardViolation, match="не ответил") as e:
            bridge.call("tabs", book="x")
        assert bridge.process.poll() is not None
    assert e.value.severity == GuardViolation.COVERAGE


# --- правки по ревью Codex этапа 5 ---

def test_rows_added_by_a_human_after_sizes_were_read_are_seen():
    lark, bridge = backend()
    lark.create_sheet("лист", ["id"])
    lark.write_rows("лист", [(None, {"id": "H-1"})])
    bridge._insert("книга-проба", "s1", "200", 50)
    bridge.sheets["s1"]["grid"][229][0] = "H-2"
    assert [row["id"] for row in lark.read_rows("лист")][-1] == "H-2"


def test_bridge_error_text_is_redacted():
    text = str(bridge_error("request https://open.example.com/api/v2?token=abc123&user=7 failed for user@example.com "
                            "with key A1b2C3d4E5f6G7h8I9j0K1l2"))
    assert "request" in text and "token=abc123" not in text and "user@example.com" not in text
    assert "A1b2C3d4E5f6G7h8I9j0K1l2" not in text
