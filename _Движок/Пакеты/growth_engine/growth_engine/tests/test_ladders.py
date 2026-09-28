import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.ladders import ACTION_COORDINATES, LADDERS, UnitOfAction


def test_six_classes_with_ladders_and_coordinates():
    assert set(LADDERS) == set(ACTION_COORDINATES) == {"A", "B", "C", "D", "E", "F"}
    assert all(len(steps) >= 5 for steps in LADDERS.values())


def test_metric_instead_of_unit_rejected():
    with pytest.raises(GuardViolation) as e:
        UnitOfAction("C", {"metric": "CR1"})
    assert e.value.guard == 6


def test_web_block_accepted():
    unit = UnitOfAction("A", {"url": "/catalog/item/", "element": "форма подбора, шаг 2"})
    assert unit.source_class == "A"


def test_dialog_pattern_needs_twenty_examples():
    with pytest.raises(GuardViolation, match="≥20"):
        UnitOfAction("F", {"stage": "возражение", "pattern": "дорого после расчёта монтажа",
                           "examples_n": 12, "base_n": 410})
    UnitOfAction("F", {"stage": "возражение", "pattern": "дорого после расчёта монтажа",
                       "examples_n": 34, "base_n": 410})


def test_base_smaller_than_examples_rejected():
    with pytest.raises(GuardViolation, match="знаменатель"):
        UnitOfAction("F", {"stage": "решение", "pattern": "ушёл думать", "examples_n": 40, "base_n": 25})


def test_unknown_class_rejected():
    with pytest.raises(GuardViolation):
        UnitOfAction("Z", {"url": "/"})
