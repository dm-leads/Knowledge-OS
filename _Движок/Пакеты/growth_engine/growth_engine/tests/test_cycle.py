"""Движок цикла: скоринг гипотезы — вера, сложность и относительный приоритет (эффект ₽ × вера ÷ сложность)."""
from dataclasses import replace

import pytest

from growth_engine.core.cycle import CREDIBILITY_FACTORS, EFFORT_FACTORS, score
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.tests.helpers import EFFECT_RUB, n


def factors(names, value):
    return {name: value for name in names}


# По построению: вера — среднее шести подфакторов ÷ 5, сложность — среднее пяти подфакторов.
def test_score_by_construction():
    credibility = {**factors(CREDIBILITY_FACTORS, 5), "измеримость": 2}
    priority = score(credibility, factors(EFFORT_FACTORS, 2), EFFECT_RUB)
    assert priority.credibility == pytest.approx((5 * 5 + 2) / 6 / 5)
    assert priority.effort == pytest.approx(2.0)
    assert priority.value == pytest.approx(EFFECT_RUB.value * priority.credibility / priority.effort)


def test_higher_credibility_ranks_higher():
    low = score(factors(CREDIBILITY_FACTORS, 2), factors(EFFORT_FACTORS, 3), EFFECT_RUB)
    high = score(factors(CREDIBILITY_FACTORS, 4), factors(EFFORT_FACTORS, 3), EFFECT_RUB)
    assert high.value > low.value


# Страж 9: пропущенный или лишний подфактор — молчаливое допущение.
@pytest.mark.parametrize("credibility", [
    {name: 3 for name in CREDIBILITY_FACTORS[:-1]},
    {**factors(CREDIBILITY_FACTORS, 3), "охват": 3},
])
def test_missing_or_extra_factor_rejected(credibility):
    with pytest.raises(GuardViolation) as e:
        score(credibility, factors(EFFORT_FACTORS, 3), EFFECT_RUB)
    assert e.value.guard == 9


@pytest.mark.parametrize("value", [0, 6, 2.5, True, "3"])
def test_factor_outside_scale_rejected(value):
    with pytest.raises(GuardViolation) as e:
        score(factors(CREDIBILITY_FACTORS, 3), {**factors(EFFORT_FACTORS, 3), "риск": value}, EFFECT_RUB)
    assert e.value.guard == 9


# Страж 8: эффект в ₽ только из модели.
def test_effect_not_from_model_stops():
    counted_in_chat = replace(EFFECT_RUB, source="D:чат:прикидка")
    with pytest.raises(GuardViolation) as e:
        score(factors(CREDIBILITY_FACTORS, 3), factors(EFFORT_FACTORS, 3), counted_in_chat)
    assert e.value.guard == 8


def test_effect_in_units_is_not_money():
    with pytest.raises(GuardViolation) as e:
        score(factors(CREDIBILITY_FACTORS, 3), factors(EFFORT_FACTORS, 3), replace(EFFECT_RUB, unit="шт"))
    assert e.value.guard == 8


# Без эффекта в ₽ приоритет не считается — вера и сложность остаются.
@pytest.mark.parametrize("effect", [None, replace(EFFECT_RUB, status=Status.NO_DATA, value=None)])
def test_without_effect_priority_is_not_counted(effect):
    priority = score(factors(CREDIBILITY_FACTORS, 4), factors(EFFORT_FACTORS, 2), effect)
    assert priority.value is None and priority.credibility == pytest.approx(0.8) and "не считается" in priority.note


def test_priority_note_carries_effect_status():
    assert "оценка" in score(factors(CREDIBILITY_FACTORS, 3), factors(EFFORT_FACTORS, 3), EFFECT_RUB).note


def test_count_is_not_effect():
    with pytest.raises(GuardViolation):
        score(factors(CREDIBILITY_FACTORS, 3), factors(EFFORT_FACTORS, 3), n("leads", 10))
