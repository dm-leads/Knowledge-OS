"""1.8.0, страж 5: порог шума гипотезы — шум разницы двух долей (база до старта и окно теста), а не одной доли.

Ревью второй моделью 18.11.2026: шум одной доли 2·√(p(1−p)/N) при равных объёмах давал ~7,9 % ложных побед вместо
~2,3 %; ожидание, равное порогу, — мощность 50 %; фактический N меньше ожидаемого занижал порог замера. Правило:
SE_D = √(p(1−p)·(1/N_base + 1/N_test)), порог вердикта 2·SE_D, минимум ожидания для запуска 2,842·SE_D (мощность 80 %).
Записи, запущенные до 1.8.0, читаются по правилу своего запуска (страж 7)."""
import math
from dataclasses import replace

import pytest

from growth_engine.core.arithmetic import ratio
from growth_engine.core.cycle import apply_noise_gate, measure
from growth_engine.core.economy import (NOISE_MIN_EFFECT_Z, is_legacy_noise, noise, noise_diff, noise_min_effect,
                                        share_base)
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Number, Status
from growth_engine.core.registry import CHANGE_ABSOLUTE, HStatus, check_state, transition
from growth_engine.core.storage import hypothesis_from_row, hypothesis_row, number_row, numbers_by_id
from growth_engine.tests.helpers import (CFG, LAUNCH, MEASURED_ON, NOISE_SHARE, TEST_WINDOW, candidate, gated, n)

# p = 0,02 на 2 000 наблюдений базы: 40 оплат на 2 000 New First SQL.
SHARE_2PCT = ratio(n("sales", 40), n("sales_entry", 2000), "c1", CFG)


def launched(**changes):
    return transition(gated(), HStatus.IN_TEST, **{**LAUNCH, **changes})


def measured_share(leads, visits):
    """Замер главной доли (CR1 веб) за окно теста 20.09–18.10.2026; знаменатель — фактический N теста."""
    return ratio(n("leads", leads, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON),
                 n("visits", visits, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON), "cr1", CFG)


# --- (а) формула разницы на известных числах ---

def test_difference_formula_on_known_numbers():
    """p = 0,02, N_base = N_test = 2 000: SE_D = √(0,02·0,98·(1/2000 + 1/2000)) = 0,004427."""
    threshold = noise_diff(SHARE_2PCT, 2000)
    se_d = math.sqrt(0.02 * 0.98 * (1 / 2000 + 1 / 2000))
    assert se_d == pytest.approx(0.004427, abs=1e-6)
    assert threshold.value == pytest.approx(2 * se_d) and threshold.value == pytest.approx(0.008854, abs=1e-6)
    assert noise_min_effect(threshold.value) == pytest.approx(NOISE_MIN_EFFECT_Z * se_d)
    assert noise_min_effect(threshold.value) == pytest.approx(0.012578, abs=1e-5)   # 2,842·SE_D = 0,012582
    assert threshold.denominator == "разница долей: база N=2000, тест N=2000" and threshold.status is Status.FACT
    # шум одной доли на тех же числах — в √2 раз меньше: прежний порог занижен
    assert noise(SHARE_2PCT, base=2000).value == pytest.approx(threshold.value / math.sqrt(2))


def test_gate_assumes_test_n_equal_to_base_and_marks_estimate():
    threshold = noise_diff(SHARE_2PCT)
    assert threshold.value == pytest.approx(noise_diff(SHARE_2PCT, 2000).value)
    assert threshold.status is Status.ESTIMATE and "принят равным N базы" in threshold.missing


def test_unknown_base_n_is_no_data_not_single_share_fallback():
    """Знаменатель не «метрика=N» (доля из источника с именем базы без числа) — N_base неизвестен: «нет данных»."""
    share = Number(metric="c1", level="sale/sale_entry", scope="p1", flow="all", period_start=SHARE_2PCT.period_start,
                   period_end=SHARE_2PCT.period_end, as_of=SHARE_2PCT.as_of, source=SHARE_2PCT.source,
                   status=Status.FACT, value=0.02, unit="доля", denominator="sales_entry")
    assert share_base(share) is None
    result = noise_diff(share, 2000)
    assert result.status is Status.NO_DATA and result.value is None and "N базы неизвестен" in result.missing
    h = apply_noise_gate(candidate(main_metric="c1"), share, 0.05, CHANGE_ABSOLUTE)
    assert h.status is HStatus.RESEARCH and "N базы неизвестен" in h.status_reason


def test_normal_approximation_is_checked_for_test_sample_too():
    """База 2 000 при p = 0,02 даёт N·p = 40, но тест на 200 — N·p = 4 < 5: порог не определён."""
    result = noise_diff(SHARE_2PCT, 200)
    assert result.status is Status.NO_DATA and "выборка теста" in result.missing and "неприменимо" in result.missing


# --- (б) ожидание между порогом и минимумом мощности — research ---

def test_gate_sends_underpowered_expectation_to_research():
    """Ожидание 1,00 п.п.: выше порога 0,89 п.п., ниже минимума 1,26 п.п. — тест его не увидит."""
    h = apply_noise_gate(candidate(main_metric="c1"), SHARE_2PCT, 0.010, CHANGE_ABSOLUTE)
    assert h.status is HStatus.RESEARCH
    assert "тест не увидит такой эффект при этом объёме (мощность < 80 %)" in h.status_reason
    assert "1,26 п.п." in h.status_reason and "0,89 п.п." in h.status_reason and "страж 5" in h.status_reason
    assert apply_noise_gate(candidate(main_metric="c1"), SHARE_2PCT, 0.013, CHANGE_ABSOLUTE).status \
        is HStatus.CANDIDATE


def test_launch_stops_underpowered_expectation_at_expected_n():
    """При N теста 9 000 порог 0,27 п.п., минимум 0,39 п.п.: ожидание 0,35 п.п. — research, а не тест."""
    with pytest.raises(GuardViolation, match="мощность < 80 %") as e:
        transition(gated(change=0.0035), HStatus.IN_TEST, **LAUNCH)
    assert e.value.guard == 5


# --- (в) замер: порог пересчитывается по фактическому N и берётся большее ---

def test_measure_recomputes_threshold_at_smaller_actual_n():
    """Ожидали 9 000 визитов, пришло 3 000: порог по факту 0,46 п.п. выше объявленного 0,30 п.п. Изменение +0,37 п.п.
    раньше засчитывалось бы в порог, теперь — нет; в причине оба порога."""
    result = measure(launched(), measured_share(58, 3000))
    delta = 58 / 3000 - 1578 / 101146
    assert 0.003 < delta < noise_diff(NOISE_SHARE, 3000).value
    assert result.status is HStatus.MEASURED and result.in_threshold is False
    assert "объявлен до старта 0,30 п.п." in result.status_reason
    assert "по фактическому N = 3000 — 0,46 п.п." in result.status_reason
    assert "порог ± 0,46 п.п." in result.status_reason and result.threshold == 0.003   # объявленное не переписано


def test_measure_without_actual_n_does_not_count_new_record():
    """Codex 18.11.2026: тот же замер без N в знаменателе не должен проходить по объявленному порогу."""
    m = measured_share(58, 3000)
    unknown = replace(m, denominator="visits")
    result = measure(launched(), unknown)
    assert result.in_threshold is False and "в порог не засчитано" in result.status_reason


def test_launch_power_minimum_counts_declared_threshold_above_noise():
    """Codex 18.11.2026: объявленный порог 0,40 п.п. выше шума — ожидание 0,40 п.п. даёт мощность ~50 %, не 80 %."""
    with pytest.raises(GuardViolation) as e:
        transition(gated(), HStatus.IN_TEST, **{**LAUNCH, "threshold": 0.004})
    assert e.value.guard == 5 and "мощность < 80 %" in str(e.value)


def test_measure_keeps_declared_threshold_when_actual_n_is_larger():
    result = measure(launched(), measured_share(2000, 101146))   # +0,42 п.п. при пороге по факту 0,11 п.п.
    assert result.in_threshold is True and "порог ± 0,30 п.п." in result.status_reason


def test_measure_does_not_count_when_actual_n_too_small_for_normal_approximation():
    result = measure(launched(), measured_share(10, 200))   # N·p базы на 200 — 3,1 < 5
    assert result.in_threshold is False and "в порог не засчитано" in result.status_reason


# --- (г) реестр, записанный до 1.8.0 ---

def legacy_launched():
    """Запуск до 1.8.0: порог — шум одной доли на ожидаемом N теста, ожидание 0,30 п.п. и порог 0,27 п.п. проходили
    старое правило (шум 0,26 п.п.), но не проходят новое (порог 0,27 п.п., минимум 0,39 п.п.)."""
    h = launched()
    return replace(h, expected_delta=0.003, threshold=0.0027, noise_threshold=noise(NOISE_SHARE, base=9000))


def test_legacy_registry_record_reads_without_error():
    h = legacy_launched()
    assert is_legacy_noise(h.noise_threshold) and not is_legacy_noise(launched().noise_threshold)
    check_state(h)   # не падает: сверка по правилу своего запуска
    row, numbers = hypothesis_row(h)
    restored = hypothesis_from_row(row, numbers_by_id(number_row(x) for x in numbers))
    assert restored == h
    check_state(restored)


def test_same_record_with_new_threshold_format_is_rechecked_by_new_rule():
    h = legacy_launched()
    renamed = replace(h, noise_threshold=replace(h.noise_threshold, denominator="разница долей: база N=101146, тест "
                                                                                 "N=9000"))
    with pytest.raises(GuardViolation) as e:
        check_state(renamed)
    assert e.value.guard == 5


def test_legacy_record_measure_is_only_tightened():
    """Замер старой записи: объявленный порог 0,27 п.п. не переписан, но по фактическому N 3 000 — 0,46 п.п."""
    result = measure(legacy_launched(), measured_share(58, 3000))
    assert result.in_threshold is False and result.threshold == 0.0027
    assert "объявлен до старта 0,27 п.п." in result.status_reason and "0,46 п.п." in result.status_reason
    check_state(result)
