"""Числа снимка модели из хранилища (задача 7.1): последний съём денежной системы, фильтры, чужие числа не показываются,
пустая выборка — код 1, команда ничего не пишет."""
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from growth_engine.core.number import Status
from growth_engine.core.storage import RegistryStore
from growth_engine.snapshot import run, select
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.fake_lark import FakeLarkBridge
from growth_engine.tests.helpers import AS_OF, AUG, LATER, n
from growth_engine.tests.test_health_core import P1, P2, snapshot

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 15)
TREE_GOAL = n("sales_entry", 1315, status=Status.ESTIMATE, source="C:модель:цель окна")
FOREIGN = n("sales_entry", 999, scope="p1", source="D:crm-y:deals", as_of=LATER)


def store_with(folder, numbers):
    RegistryStore(MarkdownBackend(folder), today=lambda: TODAY).create_numbers(numbers)
    return folder


def call(folder, **changes):
    args = Namespace(config=str(FIXTURES / "config_gate.yaml"), metric=None, scope=None, flow=None, segment=None,
                     all_takes=False, out=None if folder is None else str(folder), lark_book=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    code = run(args, out=lines.append, today=lambda: TODAY, bridge_factory=FakeLarkBridge)
    return code, lines


# --- выборка ---

def test_only_the_latest_take_of_the_money_system():
    numbers = snapshot(P1) + [TREE_GOAL, FOREIGN, replace(P1["users"], value=1800, as_of=date(2026, 9, 1))]
    chosen = select(numbers, "analytics-x", all_takes=False, filters=dict.fromkeys(("metric", "scope", "flow", "segment")))
    assert {x.as_of for x in chosen} == {AS_OF} and all(x.source_system == "analytics-x" for x in chosen)
    assert len(chosen) == len(snapshot(P1))


def test_all_takes_and_filters():
    numbers = snapshot(P1) + [replace(P1["users"], value=1800, as_of=date(2026, 9, 1))]
    chosen = select(numbers, "analytics-x", all_takes=True, filters={"metric": "sales_entry", "scope": "p1",
                                                                    "flow": None, "segment": None})
    assert [(x.value, x.as_of) for x in chosen] == [(1800.0, date(2026, 9, 1)), (1860.0, AS_OF)]


# --- команда ---

def test_prints_the_latest_take_with_status_and_writes_nothing(tmp_path):
    folder = store_with(tmp_path / "хранилище", snapshot(P1, P2) + [TREE_GOAL, FOREIGN])
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    code, lines = call(folder)
    text = "\n".join(lines)
    assert code == 0 and lines[0].startswith("Снимок модели: хранилище markdown") and "последний съём" in lines[0]
    assert "статус" in text and "источник C:analytics-x:data" in text
    assert "C:модель:цель окна" not in text and "D:crm-y:deals" not in text
    assert lines[-1].startswith("ИТОГ: чисел ") and "съёмы: 03.09.2026" in lines[-1]
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before


def test_filter_by_metric_and_scope(tmp_path):
    folder = store_with(tmp_path / "хранилище", snapshot(P1, P2))
    code, lines = call(folder, metric="ampu", scope="p1")
    assert code == 0 and "metric = ampu, scope = p1" in lines[0]
    assert sum(line.startswith("   ") for line in lines) == 1 and "ampu = " in lines[1]


def test_unknown_metric_is_code_1(tmp_path):
    code, lines = call(store_with(tmp_path / "хранилище", snapshot(P1)), metric="нет такой метрики")
    assert code == 1 and "показывать нечего" in lines[-1]


def test_store_with_cycle_model_numbers_only_is_code_1(tmp_path):
    code, lines = call(store_with(tmp_path / "хранилище", [TREE_GOAL]))
    assert code == 1 and "показывать нечего" in lines[-1]


def test_money_system_must_be_declared_and_not_the_cycle_model(tmp_path):
    raw = yaml.safe_load((FIXTURES / "config_gate.yaml").read_text(encoding="utf-8"))
    raw["economy"]["money_system"] = "модель"
    config = tmp_path / "конфигурация.yaml"
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    code, lines = call(store_with(tmp_path / "хранилище", snapshot(P1)), config=str(config))
    assert code == 1 and "модель" in lines[-1] and "[страж 9]" in lines[-1]


# С3: команда шага 1 берёт хранилище из конфигурации инстанса — без ключа, как её зовёт скилл.
def test_store_comes_from_the_instance_config_without_a_key(tmp_path):
    folder = store_with(tmp_path / "хранилище", snapshot(P1))
    raw = yaml.safe_load((FIXTURES / "config_gate.yaml").read_text(encoding="utf-8"))
    raw["storage"] = {"adapter": "lark_sheets", "book": "книга-проба"}
    config = tmp_path / "конфигурация.yaml"
    config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    code, lines = call(None, config=str(config))
    assert code == 1 and "книга-проба" in lines[0]
    code, lines = call(folder, config=str(config))
    assert code == 0 and "markdown" in lines[0]


def test_store_not_declared_anywhere_is_code_1(tmp_path):
    code, lines = call(None)
    assert code == 1 and "хранилище не задано" in lines[-1]
