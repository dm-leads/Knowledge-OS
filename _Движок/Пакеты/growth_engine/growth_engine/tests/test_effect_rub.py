"""Страж 8: эффект гипотезы в ₽ — только из движка экономики: единицы эффекта × деньги на ту же единицу той же системы."""
from dataclasses import replace

import pytest

from growth_engine.core.economy import effect_rub, per_unit
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.core.registry import HStatus, create, transition
from growth_engine.tests.helpers import CARD, CFG, FORMULA, n


def ampu(scope="p1", source="C:analytics-x:data"):
    profit = replace(n("gross_profit", 27_179_371, scope=scope, source=source), unit="₽")
    return per_unit(profit, n("sales_entry", 1994, scope=scope, source=source), "ampu", CFG)


def test_effect_in_goal_units_to_rubles():
    result = effect_rub(n("sales_entry", 12, status=Status.ESTIMATE), ampu())
    assert result.value == pytest.approx(12 * 27_179_371 / 1994)
    assert (result.unit, result.status, result.source_system) == ("₽", Status.ESTIMATE, "модель")
    assert "12 sales_entry × ampu" in result.missing


def test_effect_is_never_above_estimate():
    assert effect_rub(n("sales_entry", 12), ampu()).status is Status.ESTIMATE


def test_effect_units_must_match_money_per_unit():
    with pytest.raises(GuardViolation) as e:
        effect_rub(n("leads", 12, status=Status.ESTIMATE), ampu())
    assert e.value.guard == 4


# П2: людей другой системы (когорта CRM) на деньги системы денежной модели не умножаем.
def test_effect_from_other_system_is_not_converted():
    with pytest.raises(GuardViolation) as e:
        effect_rub(n("sales_entry", 12, status=Status.ESTIMATE, source="D:crm-y:leads"), ampu())
    assert e.value.guard == 4


def test_effect_and_money_per_unit_of_one_cabinet():
    with pytest.raises(GuardViolation) as e:
        effect_rub(n("sales_entry", 12, scope="p2", status=Status.ESTIMATE), ampu())
    assert e.value.guard == 4


def test_multiplier_must_be_money_per_unit():
    with pytest.raises(GuardViolation) as e:
        effect_rub(n("sales_entry", 12, status=Status.ESTIMATE), replace(n("gross_profit", 1000), unit="₽"))
    assert e.value.guard == 4


def test_effect_without_data_is_no_data():
    result = effect_rub(n("sales_entry", None, status=Status.NO_DATA), ampu())
    assert result.status is Status.NO_DATA and result.value is None


# Страж 8: карточка реестра принимает эффект, посчитанный движком экономики.
def test_registry_card_accepts_effect_from_economy():
    effect = n("sales_entry", 12, status=Status.ESTIMATE)
    h = transition(create(id="H-100", formulation=FORMULA), HStatus.RESEARCH)
    card = {**CARD, "effect_goal_units": effect, "effect_rub": effect_rub(effect, ampu())}
    assert transition(h, HStatus.CANDIDATE, **card).effect_rub.source_system == "модель"
