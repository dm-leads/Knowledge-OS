"""RAT (шаг 8): приоритет допущения = P × цена ошибки ÷ цена проверки; допущение, которое можно убрать, — первым.

Канон Next Move Theory, rat-key-theses §8 (формула упорядочивает, а не оценивает) и §10 (сначала — что убрать).
"""
import math

import pytest

from growth_engine.core.cycle import Assumption, order_assumptions, rat_priority
from growth_engine.core.errors import GuardViolation


def test_priority_by_construction():
    assert rat_priority(0.5, 1000, 100) == pytest.approx(5.0)
    assert rat_priority(0, 1000, 100) == 0


# Одинаковые риск и цена ошибки — первой идёт более дешёвая проверка.
def test_cheaper_check_goes_first():
    costly = Assumption("посетители бросают форму на шаге 2", p_wrong=0.4, cost_of_error=900, cost_of_check=300)
    cheap = Assumption("менеджер перезванивает в течение часа", p_wrong=0.4, cost_of_error=900, cost_of_check=30)
    assert order_assumptions([costly, cheap]) == (cheap, costly)


def test_removable_assumption_goes_first_even_with_lower_priority():
    risky = Assumption("сегмент платит за монтаж отдельно", p_wrong=0.9, cost_of_error=5000, cost_of_check=10)
    removable = Assumption("для заявки нужен звонок менеджера", p_wrong=0.1, cost_of_error=10, cost_of_check=100,
                           removable=True)
    assert order_assumptions([risky, removable]) == (removable, risky)


def test_equal_priority_keeps_written_order():
    first = Assumption("первое допущение", p_wrong=0.5, cost_of_error=100, cost_of_check=10)
    second = Assumption("второе допущение", p_wrong=0.5, cost_of_error=100, cost_of_check=10)
    assert order_assumptions([first, second]) == (first, second)


@pytest.mark.parametrize("p", [-0.1, 1.1, math.nan, math.inf, True, "0.5"])
def test_probability_outside_unit_interval_rejected(p):
    with pytest.raises(GuardViolation) as e:
        rat_priority(p, 1000, 100)
    assert e.value.guard == 9


@pytest.mark.parametrize("cost", [0, -1, math.nan, math.inf])
def test_check_cost_must_be_positive_and_finite(cost):
    with pytest.raises(GuardViolation) as e:
        rat_priority(0.5, 1000, cost)
    assert e.value.guard == 9


def test_negative_cost_of_error_rejected():
    with pytest.raises(GuardViolation) as e:
        rat_priority(0.5, -1, 100)
    assert e.value.guard == 9


def test_assumption_without_statement_rejected():
    with pytest.raises(GuardViolation) as e:
        order_assumptions([Assumption("  ", p_wrong=0.5, cost_of_error=100, cost_of_check=10)])
    assert e.value.guard == 9


def test_invalid_assumption_in_list_stops_ordering():
    with pytest.raises(GuardViolation):
        order_assumptions([Assumption("проверка бесплатна", p_wrong=0.5, cost_of_error=100, cost_of_check=0)])
