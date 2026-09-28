"""Известные ответы и пересмотры задним числом в ядре гейта (задача 6.3): закрытые месяцы, сравнение с последним более
ранним съёмом той же системы, известный ответ против живого числа."""
from dataclasses import replace
from datetime import date

import pytest

from growth_engine.core.economy import Tolerance
from growth_engine.core.errors import GuardViolation
from growth_engine.core.health import Outcome, check_known_answer, check_revisions, closed_months
from growth_engine.core.number import Status
from growth_engine.tests.helpers import AS_OF, AUG, JUL, LATER, n
from growth_engine.tests.test_health_core import kinds


def test_closed_months_before_the_current_one():
    assert closed_months(date(2026, 9, 15), 2) == [JUL, AUG]
    assert closed_months(date(2026, 1, 10), 2) == [(date(2025, 11, 1), date(2025, 12, 1)),
                                                   (date(2025, 12, 1), date(2026, 1, 1))]
    with pytest.raises(GuardViolation) as e:
        closed_months(date(2026, 9, 15), 0)
    assert e.value.guard == 9


@pytest.mark.parametrize("value, guard, outcome", [(1860, 2, Outcome.PASSED), (1875, 2, Outcome.PASSED),
                                                   (1900, 2, Outcome.STRUCTURE), (None, 13, Outcome.COVERAGE)])
def test_known_answer_against_a_live_number(value, guard, outcome):
    number = n("sales_entry", 0, as_of=LATER)
    number = replace(number, value=value) if value is not None else \
        replace(number, value=None, status=Status.NO_DATA, missing="окна нет в ответе")
    check = check_known_answer(number, 1860, Tolerance(0, 0.01), "снимок 03.09.2026")
    assert (check.guard, check.outcome) == (guard, outcome) and "против известного 1 860 шт" in check.text


def test_revisions_compare_with_the_latest_earlier_take_of_the_same_system():
    stored = [n("sales_entry", 940, scope="p1+p2", period=JUL, as_of=date(2026, 9, 1)),
              n("sales_entry", 950, scope="p1+p2", period=JUL, as_of=AS_OF),
              n("sales_entry", 1000, scope="p1+p2", period=JUL, as_of=AS_OF, source="C:модель:цель окна")]
    fresh = [n("sales_entry", 951, scope="p1+p2", period=JUL, as_of=LATER),
             n("sales_entry", 1994, scope="p1+p2", period=AUG, as_of=LATER)]
    checks = check_revisions(stored, fresh, Tolerance(2, 0.01))
    assert kinds(checks) == [(13, Outcome.PASSED), (13, Outcome.COVERAGE)]
    assert "съём 03.09.2026 — 950, съём 14.09.2026 — 951" in checks[0].text and "нет съёма этого окна" in checks[1].text
    same_day = check_revisions([replace(fresh[1], value=1990)], fresh[1:], Tolerance(2, 0.01))
    assert kinds(same_day) == [(13, Outcome.NOT_CHECKED)]
