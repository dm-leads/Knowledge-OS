"""1.9.3: схема A/B — порог по двум плечам одного окна, запуск по нему, замер «вариант против контроля»."""
import math
from dataclasses import replace
from datetime import date

import pytest

from growth_engine.core.arithmetic import ratio
from growth_engine.core.cycle import apply_noise_gate, measure
from growth_engine.core.economy import noise_ab
from growth_engine.core.errors import GuardViolation
from growth_engine.core.registry import CHANGE_ABSOLUTE, HStatus, transition
from growth_engine.tests.helpers import CFG, LAUNCH, NOISE_SHARE, candidate, n

P = NOISE_SHARE.value
WINDOW = (date(2026, 9, 20), date(2026, 10, 18))
ON = date(2026, 10, 19)


def formula(p, n_a, n_b):
    """Порог, посчитанный в тесте независимо от движка: 2·√(p(1−p)·(1/N_A + 1/N_B))."""
    return 2 * math.sqrt(p * (1 - p) * (1 / n_a + 1 / n_b))


def arm(leads, visits, value):
    share = ratio(n("leads", leads, flow="web", period=WINDOW, as_of=ON),
                  n("visits", visits, flow="web", period=WINDOW, as_of=ON), "cr1", CFG)
    return replace(share, segment=f"ab_h=" + value)


def test_noise_ab_matches_independent_formula():
    assert noise_ab(NOISE_SHARE, 9000, 9000).value == pytest.approx(formula(P, 9000, 9000))
    with pytest.raises(GuardViolation, match="N плеча"):
        noise_ab(NOISE_SHARE, 0, 9000)


def test_gate_ab_needs_arm_n_and_stamps_design():
    with pytest.raises(GuardViolation, match="arm_n"):
        apply_noise_gate(candidate(), NOISE_SHARE, 0.0045, CHANGE_ABSOLUTE, "ab")
    h = apply_noise_gate(candidate(), NOISE_SHARE, 0.0045, CHANGE_ABSOLUTE, "ab", 20000, "ab_h=B", "ab_h=A")
    assert h.test_design == "ab" and h.noise_threshold.value == pytest.approx(formula(P, 20000, 20000))
    with pytest.raises(GuardViolation, match="схема теста"):
        apply_noise_gate(candidate(), NOISE_SHARE, 0.0045, CHANGE_ABSOLUTE, "split")


def test_launch_ab_checks_threshold_on_arm_n_and_design_is_declared_only_by_gate():
    h = apply_noise_gate(candidate(), NOISE_SHARE, 0.0045, CHANGE_ABSOLUTE, "ab", 20000, "ab_h=B", "ab_h=A")
    launched = transition(h, HStatus.IN_TEST, **{**LAUNCH, "expected_n": 20000})   # N — визиты одного плеча
    assert launched.status is HStatus.IN_TEST
    with pytest.raises(GuardViolation, match="меньше шума|ниже шума"):
        transition(h, HStatus.IN_TEST, **{**LAUNCH, "expected_n": 1000, "threshold": 0.003})
    with pytest.raises(GuardViolation, match="test_design"):
        transition(apply_noise_gate(candidate(), NOISE_SHARE, 0.0045, CHANGE_ABSOLUTE), HStatus.IN_TEST,
                   **{**LAUNCH, "test_design": "ab"})


def test_measure_ab_is_variant_minus_control_of_the_same_window():
    h = transition(apply_noise_gate(candidate(), NOISE_SHARE, 0.0045, CHANGE_ABSOLUTE, "ab", 20000, "ab_h=B", "ab_h=A"),
                   HStatus.IN_TEST, **{**LAUNCH, "expected_n": 20000})
    control, variant = arm(312, 20000, "A"), arm(420, 20000, "B")    # 1,56 % и 2,10 %: +0,54 п.п.
    got = measure(h, variant, control)
    assert got.status is HStatus.MEASURED and got.in_threshold is True and "ab_h=B" in got.status_reason
    with pytest.raises(GuardViolation, match="control_id"):
        measure(h, variant)
    with pytest.raises(GuardViolation, match="объявлены гейтом"):
        measure(h, control, variant)                    # перестановка плеч — стоп, а не «в пороге»
    assert got.control is control


def test_gate_ab_needs_declared_segments_and_plain_gate_refuses_ab_fields():
    with pytest.raises(GuardViolation, match="variant и control"):
        apply_noise_gate(candidate(), NOISE_SHARE, 0.0045, CHANGE_ABSOLUTE, "ab", 20000, "ab_h=B", "ab_h=B")
    with pytest.raises(GuardViolation, match="только для схемы A/B"):
        apply_noise_gate(candidate(), NOISE_SHARE, 0.0045, CHANGE_ABSOLUTE, "", 20000)
