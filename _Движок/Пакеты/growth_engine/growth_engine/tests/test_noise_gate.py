"""Страж 5: ожидаемое изменение меньше порога шума — research кодом. Изменение объявляется один раз — гейтом шума, с
видом изменения (абсолютное в долях или относительное); при запуске реестр пересчитывает порог из той же доли на
ожидаемом N теста, проверяет давность доли и не даёт переписать объявленное гейтом."""
import math
from dataclasses import replace
from datetime import date

import pytest

from growth_engine.core.arithmetic import ratio
from growth_engine.core.cycle import apply_noise_gate
from growth_engine.core.economy import noise
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.core.registry import CHANGE_ABSOLUTE, CHANGE_RELATIVE, HStatus, transition
from growth_engine.tests.helpers import CFG, LAUNCH, MAY, NOISE_SHARE, candidate, gated, n

# Доля модели v1.0, §4: 712 покупателей на 1 994 New First SQL — порог шума 2,15 п.п. на базе доли.
SHARE = ratio(n("sales", 712), n("sales_entry", 1994), "c1", CFG)
C1 = dict(main_metric="c1")   # главная метрика гипотезы — та же доля, что и у порога
ABS, REL = CHANGE_ABSOLUTE, CHANGE_RELATIVE


def formula(p, observations):
    """Порог шума, посчитанный в тесте независимо от движка: 2·√(p(1−p)/N)."""
    return 2 * math.sqrt(p * (1 - p) / observations)


def launch(h, **changes):
    return transition(h, HStatus.IN_TEST, **{**LAUNCH, **changes})


# --- гейт до запуска ---

def test_expected_change_below_noise_goes_to_research():
    h = apply_noise_gate(candidate(**C1), SHARE, 0.01, ABS)
    assert h.status is HStatus.RESEARCH and "страж 5" in h.status_reason and "2,15 п.п." in h.status_reason
    assert h.noise_threshold.value == pytest.approx(formula(712 / 1994, 1994)) and h.noise_share == SHARE
    assert (h.expected_delta, h.expected_kind) == (0.01, ABS)


def test_expected_change_above_noise_stays():
    assert apply_noise_gate(candidate(**C1), SHARE, 0.05, ABS).status is HStatus.CANDIDATE


def test_negative_expected_change_is_compared_by_size():
    assert apply_noise_gate(candidate(**C1), SHARE, -0.05, ABS).status is HStatus.CANDIDATE


# Ревью Codex 15.09: «рост 10%» к доле 35,71% — это +3,57 п.п., а не +10 п.п.; переводит код.
def test_relative_change_is_converted_to_points_by_code():
    h = apply_noise_gate(candidate(**C1), SHARE, 0.10, REL)
    assert h.status is HStatus.CANDIDATE and h.expected_delta == pytest.approx(712 / 1994 * 0.10)
    assert h.expected_kind == REL
    assert apply_noise_gate(candidate(**C1), SHARE, 0.05, REL).status is HStatus.RESEARCH   # +1,79 п.п. < 2,15


@pytest.mark.parametrize("kind", ["", "п.п.", None])
def test_change_kind_must_be_declared(kind):
    with pytest.raises(GuardViolation) as e:
        apply_noise_gate(candidate(**C1), SHARE, 0.05, kind)
    assert e.value.guard == 9


def test_noise_without_data_goes_to_research_with_reason():
    share = ratio(n("sales", None, status=Status.NO_DATA), n("sales_entry", 1994), "c1", CFG)
    h = apply_noise_gate(candidate(**C1), share, 0.05, ABS)
    assert h.status is HStatus.RESEARCH and "не определён" in h.status_reason


def test_relative_change_without_share_goes_to_research():
    share = ratio(n("sales", None, status=Status.NO_DATA), n("sales_entry", 1994), "c1", CFG)
    h = apply_noise_gate(candidate(**C1), share, 0.10, REL)
    assert h.status is HStatus.RESEARCH and "относительное" in h.status_reason and h.expected_delta is None


# Ревью 14.09: доля 0 или 1 давала нулевой шум, и любое ожидание проходило.
@pytest.mark.parametrize("sales, delta", [(0, 0.0001), (500, -0.0001)])
def test_extreme_share_has_no_noise_threshold_and_goes_to_research(sales, delta):
    share = ratio(n("sales", sales), n("sales_entry", 500), "c1", CFG)
    h = apply_noise_gate(candidate(**C1), share, delta, ABS)
    assert h.status is HStatus.RESEARCH and "неприменимо" in h.status_reason


def test_share_of_other_metric_is_rejected():
    with pytest.raises(GuardViolation) as e:
        apply_noise_gate(candidate(), SHARE, 0.05, ABS)
    assert e.value.guard == 5


@pytest.mark.parametrize("bad", [math.nan, math.inf, True, 1.5])
def test_expected_change_must_be_a_finite_share(bad):
    with pytest.raises(GuardViolation) as e:
        apply_noise_gate(candidate(**C1), SHARE, bad, ABS)
    assert e.value.guard == 5


# «+70 п.п.» к доле 35,7% выводит долю за 1 — изменение записано не в долях.
def test_expected_share_outside_unit_interval_rejected():
    with pytest.raises(GuardViolation, match=r"вне \[0; 1\]"):
        apply_noise_gate(candidate(**C1), SHARE, 0.7, ABS)


# Ревью 14.09: после запуска объявленное до старта не переписывается.
def test_gate_after_launch_rejected():
    started = launch(gated())
    with pytest.raises(GuardViolation) as e:
        apply_noise_gate(started, NOISE_SHARE, 0.5, ABS)
    assert e.value.guard == 7


# --- запуск теста ---

def test_launch_recomputes_noise_at_expected_n():
    h = launch(gated())
    assert h.noise_threshold.value == pytest.approx(formula(1578 / 101146, 9000))
    assert h.noise_threshold.denominator == "ожидаемый N теста=9000"


def test_launch_without_gate_stops():
    with pytest.raises(GuardViolation) as e:
        launch(candidate())
    assert e.value.guard == 5


# Ревью Codex 15.09: объявленное гейтом не переписывается при запуске — ни ожидание, ни вид, ни доля, ни готовый порог.
@pytest.mark.parametrize("field", ["expected_delta", "expected_kind", "noise_share", "noise_threshold"])
def test_launch_cannot_override_what_gate_declared(field):
    values = dict(expected_delta=0.5, expected_kind=ABS, noise_share=SHARE,
                  noise_threshold=replace(noise(NOISE_SHARE), value=0.00001))
    with pytest.raises(GuardViolation, match="гейтом шума") as e:
        launch(gated(), **{field: values[field]})
    assert e.value.guard == 5


# Ревью 14.09: порог по исторической базе 101 146 визитов (0,08 п.п.) не годится для теста на 9 000 (0,26 п.п.).
def test_threshold_below_noise_at_expected_n_stops():
    with pytest.raises(GuardViolation, match="N = 9000") as e:
        launch(gated(), threshold=0.0025)
    assert e.value.guard == 5


def test_expected_change_below_noise_at_launch_stops():
    with pytest.raises(GuardViolation, match="research") as e:
        launch(gated(change=0.002), threshold=0.003)
    assert e.value.guard == 5


def test_launch_share_of_other_metric_stops():
    with pytest.raises(GuardViolation, match="главная метрика") as e:
        launch(replace(gated(), main_metric="c1"))
    assert e.value.guard == 5


# Ревью Codex 15.09: доля для порога — снята до старта и не старше 92 дней.
def test_stale_share_stops():
    old = ratio(n("leads", 1578, flow="web", period=MAY), n("visits", 101146, flow="web", period=MAY), "cr1", CFG)
    with pytest.raises(GuardViolation, match="старше 92 дней") as e:
        launch(apply_noise_gate(candidate(), old, 0.004, ABS))
    assert e.value.guard == 5


def test_share_after_start_stops():
    with pytest.raises(GuardViolation, match="позже старта") as e:
        launch(gated(), start_date=date(2026, 8, 15))
    assert e.value.guard == 5


def test_start_date_must_be_a_date():
    with pytest.raises(GuardViolation) as e:
        launch(gated(), start_date="2026-09-20")
    assert e.value.guard == 7


@pytest.mark.parametrize("changes", [dict(threshold=math.nan), dict(threshold=0.0), dict(threshold=3.0),
                                     dict(expected_n=0), dict(expected_n=9000.5), dict(expected_n=50)])
def test_launch_numbers_must_be_valid(changes):
    with pytest.raises(GuardViolation) as e:
        launch(gated(), **changes)
    assert e.value.guard == 5


@pytest.mark.parametrize("expected", [math.nan, True, 0.99])
def test_stored_expected_change_is_rechecked_at_launch(expected):
    with pytest.raises(GuardViolation) as e:
        launch(replace(gated(), expected_delta=expected))
    assert e.value.guard == 5
