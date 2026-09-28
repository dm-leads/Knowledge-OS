"""Замер теста и проверка сохранённой гипотезы (задача 5.3а, решения Х9 и Х10).

`measure()` — единственный путь к статусу «замер»: доля та же, что у гейта, окно — объявленное, попадание в порог
считает код. `check_state()` проверяет гипотезу для её текущего статуса без перехода — хранилище вызывает её при записи
и при чтении.
"""
from dataclasses import replace
from datetime import date, timedelta

import pytest

from growth_engine.core.arithmetic import ratio
from growth_engine.core.cycle import measure
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.core.registry import (ZONE_OWN, ZONE_PACKAGE, Decision, HStatus, check_state, create,
                                         transition)
from growth_engine.tests.helpers import (CFG, FORMULA, LAUNCH, MEASURED_ON, TEST_WINDOW, candidate, concluded, gated,
                                         measured, n)


def launched():
    return transition(gated(), HStatus.IN_TEST, **LAUNCH)


def share(leads, period=TEST_WINDOW, as_of=MEASURED_ON, source="C:analytics-x:data", status=Status.FACT):
    return ratio(n("leads", leads, flow="web", period=period, as_of=as_of, source=source, status=status),
                 n("visits", 101146, flow="web", period=period, as_of=as_of, source=source), "cr1", CFG)


# --- замер ---

# Известный ответ фикстуры: CR1 веб 1,56% у гейта → 1,62% за окно теста; +0,06 п.п. при пороге 0,30 п.п.
def test_measurement_below_threshold_is_not_in_threshold():
    h = measure(launched(), share(1640))
    assert h.status is HStatus.MEASURED and h.in_threshold is False and "не в пороге" in h.status_reason
    assert h.in_threshold == measured().in_threshold


def test_measurement_above_threshold_in_expected_direction_is_in_threshold():
    assert measure(launched(), share(2000)).in_threshold is True       # +0,42 п.п.


def test_large_change_against_expected_direction_is_not_in_threshold():
    assert measure(launched(), share(1200)).in_threshold is False      # −0,37 п.п., ожидали рост


@pytest.mark.parametrize("make", [candidate, lambda: measure(launched(), share(1640))], ids=["candidate", "второй замер"])
def test_measurement_only_from_test_or_deferred(make):
    with pytest.raises(GuardViolation) as e:
        measure(make(), share(1700))
    assert e.value.guard == 7


@pytest.mark.parametrize("wrong", [
    n("leads", 1640, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON),                        # не доля, а счётчик
    replace(share(1640), flow="all"),                                                           # другой поток
    share(1640, period=(date(2026, 9, 21), date(2026, 10, 19)), as_of=date(2026, 10, 20)),     # окно сдвинуто
    share(1640, period=(date(2026, 9, 20), date(2026, 10, 11)), as_of=date(2026, 10, 12)),     # окно короче
    share(1640, as_of=date(2026, 10, 10)),                                                      # снят до конца окна
    share(None, status=Status.NO_DATA),                                                         # нет значения
], ids=["счётчик", "поток", "сдвиг окна", "короткое окно", "рано снят", "нет данных"])
def test_measurement_must_match_declared_share_and_window(wrong):
    with pytest.raises(GuardViolation) as e:
        measure(launched(), wrong)
    assert e.value.guard == 7


def test_measurement_from_other_system_is_mixed():
    with pytest.raises(GuardViolation) as e:
        measure(launched(), share(1640, source="C:analytics-y:data"))
    assert e.value.guard == 4


def test_deferred_measurement_waits_for_its_date():
    deferred = transition(launched(), HStatus.DEFERRED, deferred_until=date(2026, 11, 15))
    with pytest.raises(GuardViolation) as e:
        measure(deferred, share(1640))
    assert e.value.guard == 7
    assert measure(deferred, share(1640, as_of=date(2026, 11, 16))).status is HStatus.MEASURED


# --- проверка сохранённой гипотезы ---

def waiting_owner():
    return transition(gated(), HStatus.WAITING_OWNER, **{**LAUNCH, "zone": ZONE_PACKAGE})


def blocked():
    return transition(transition(create(id="H-002", formulation=FORMULA), HStatus.RESEARCH), HStatus.BLOCKED_NO_DATA,
                      blocked_class="F")


VALID = {"идея": lambda: create(id="H-000", formulation=FORMULA), "candidate": candidate, "после гейта": gated,
         "в тесте": launched, "ждёт владельца": waiting_owner,
         "отложенный замер": lambda: transition(launched(), HStatus.DEFERRED, deferred_until=date(2026, 11, 15)),
         "замер": measured, "вывод": concluded, "blocked": blocked,
         "архив": lambda: transition(concluded(), HStatus.ARCHIVE)}


@pytest.mark.parametrize("name", list(VALID))
def test_valid_hypothesis_passes_state_check(name):
    check_state(VALID[name]())


@pytest.mark.parametrize("make, guard", [
    (lambda: replace(candidate(), effect_rub=None), 6),
    (lambda: replace(candidate(), formulation="поднять конверсию"), 6),
    (lambda: replace(launched(), threshold=None), 7),
    (lambda: replace(launched(), zone="чужая"), 7),
    (lambda: replace(waiting_owner(), zone=ZONE_OWN), 7),
    (lambda: replace(launched(), noise_threshold=replace(launched().noise_threshold, value=0.00001)), 5),
    (lambda: replace(launched(), expected_kind=""), 5),
    (lambda: replace(launched(), start_date=date(2026, 9, 20) + timedelta(days=200)), 5),
    (lambda: replace(measured(), in_threshold=None), 7),
    (lambda: replace(transition(launched(), HStatus.DEFERRED, deferred_until=date(2026, 11, 15)), deferred_until=None), 7),
    (lambda: replace(concluded(), knowledge_row=""), 14),
    (lambda: replace(concluded(), decision="продлить"), 14),
    (lambda: replace(blocked(), blocked_class="Z"), 6),
], ids=["нет эффекта в ₽", "не формула", "нет порога", "чужая зона", "ждёт владельца в своей зоне",
        "порог шума подменён", "вид изменения стёрт", "доля устарела к старту", "нет попадания в порог",
        "нет даты отложенного замера", "нет строки знаний", "решение не из канона", "класс источника не из A–F"])
def test_broken_hypothesis_fails_state_check(make, guard):
    with pytest.raises(GuardViolation) as e:
        check_state(make())
    assert e.value.guard == guard


def test_decision_enum_still_canonical():
    assert concluded().decision is Decision.KILL
