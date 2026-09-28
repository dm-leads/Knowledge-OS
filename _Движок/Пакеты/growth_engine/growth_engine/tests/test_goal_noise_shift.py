"""Движок экономики: порог шума доли, окно цели, разложение доли на структуру и конверсию внутри сегментов."""
import math
from dataclasses import replace
from datetime import date

import pytest

from growth_engine.core.arithmetic import ratio
from growth_engine.core.economy import money_share, noise, required_last_month, shift_share, window_average
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.tests.helpers import AUG, CFG, JUL, JUN, LATER, MAY, n


# Известные ответы модели v1.0, §4 (июн–авг 2026): пороги шума 0,08 / 1,04 / 1,66 / 2,15 п.п.
@pytest.mark.parametrize("numerator, denominator, threshold_pp", [
    (("leads", 8064), ("visits", 210571), 0.08),
    (("qualified", 2596), ("leads", 8064), 1.04),
    (("sales_entry", 1994), ("qualified", 2596), 1.66),
    (("sales", 712), ("sales_entry", 1994), 2.15),
])
def test_noise_threshold_known_answers(numerator, denominator, threshold_pp):
    share = ratio(n(*numerator), n(*denominator), "cr", CFG)
    threshold = noise(share)
    assert round(threshold.value * 100, 2) == threshold_pp
    assert (threshold.unit, threshold.denominator, threshold.as_of) == ("доля", share.denominator, share.as_of)


def test_noise_needs_a_share_with_base():
    with pytest.raises(GuardViolation) as e:
        noise(n("leads", 8064))
    assert e.value.guard == 9


def test_noise_of_share_without_data_is_no_data():
    share = ratio(n("leads", None, status=Status.NO_DATA), n("visits", 100), "cr", CFG)
    assert noise(share).status is Status.NO_DATA


# Известный ответ 09.09.2026: окно New First SQL июн–авг — 706 + 658 + 630, среднее 664,67 → 665.
def test_window_average_known_answer():
    months = [n("sales_entry", 706, period=JUN), n("sales_entry", 658, period=JUL), n("sales_entry", 630, period=AUG)]
    average = window_average(months, CFG)
    assert round(average.value) == 665 and average.unit == "шт в месяц"
    assert (average.period_start, average.period_end) == (JUN[0], AUG[1])


# Известный ответ 10.09.2026: чтобы окно июл–сен дало 850, сентябрю нужно 3 × 850 − 658 − 630 = 1 262.
def test_required_last_month_known_answer():
    need = required_last_month([n("sales_entry", 658, period=JUL), n("sales_entry", 630, period=AUG)], 850, CFG)
    assert need.value == 1262 and need.status is Status.ESTIMATE
    assert (need.period_start, need.period_end) == (date(2026, 9, 1), date(2026, 10, 1))


def test_window_needs_configured_number_of_months():
    with pytest.raises(GuardViolation) as e:
        window_average([n("sales_entry", 658, period=JUL), n("sales_entry", 630, period=AUG)], CFG)
    assert e.value.guard == 9


def test_window_is_only_for_goal_metric():
    with pytest.raises(GuardViolation) as e:
        window_average([n("leads", 1, period=JUN), n("leads", 1, period=JUL), n("leads", 1, period=AUG)], CFG)
    assert e.value.guard == 9


# П3 и страж 3: месяцы окна — встык и одного съёма.
def test_window_months_of_different_fetch_stop():
    with pytest.raises(GuardViolation) as e:
        required_last_month([n("sales_entry", 658, period=JUL), n("sales_entry", 630, period=AUG, as_of=LATER)], 850, CFG)
    assert e.value.guard == 3


def test_window_months_must_be_contiguous():
    with pytest.raises(GuardViolation):
        window_average([n("sales_entry", 1, period=MAY), n("sales_entry", 1, period=JUL),
                        n("sales_entry", 1, period=AUG)], CFG)


def segment_numbers(data, period, as_of=None):
    """data = {сегмент: (база, события)} → списки чисел базы и событий с разрезом по сегменту."""
    extra = {} if as_of is None else {"as_of": as_of}
    base = [replace(n("visits", b, flow="web", period=period, **extra), segment=f"channel={k}") for k, (b, _) in data.items()]
    events = [replace(n("leads", e, flow="web", period=period, **extra), segment=f"channel={k}") for k, (_, e) in data.items()]
    return base, events


# Страж 4: структура + конверсия внутри сегментов = изменение доли; значения — по формуле, посчитанной в тесте.
def test_shift_share_structure_plus_within_equals_total():
    before, after = {"a": (100, 10), "b": (100, 5)}, {"a": (50, 5), "b": (150, 9)}
    result = shift_share(*segment_numbers(before, MAY), *segment_numbers(after, AUG), cfg=CFG)
    shares_before, shares_after = {"a": 0.5, "b": 0.5}, {"a": 0.25, "b": 0.75}
    rates_before, rates_after = {"a": 0.10, "b": 0.05}, {"a": 0.10, "b": 0.06}
    structure = sum((shares_after[k] - shares_before[k]) * rates_before[k] for k in "ab")
    within = sum(shares_after[k] * (rates_after[k] - rates_before[k]) for k in "ab")
    assert result.structure.value == pytest.approx(structure) and result.within.value == pytest.approx(within)
    assert result.total.value == pytest.approx(14 / 200 - 15 / 200)
    assert result.total.unit == "доля" and result.total.period_start == MAY[0] and result.total.period_end == AUG[1]


def test_shift_share_needs_same_segments_in_both_periods():
    with pytest.raises(GuardViolation) as e:
        shift_share(*segment_numbers({"a": (100, 10), "b": (100, 5)}, MAY),
                    *segment_numbers({"a": (50, 5), "c": (150, 9)}, AUG), cfg=CFG)
    assert e.value.guard == 4


def test_shift_share_rejects_segment_with_zero_base():
    with pytest.raises(GuardViolation) as e:
        shift_share(*segment_numbers({"a": (100, 10), "b": (0, 0)}, MAY),
                    *segment_numbers({"a": (50, 5), "b": (150, 9)}, AUG), cfg=CFG)
    assert e.value.guard == 4


# П3: окна сравнения — одного съёма.
def test_shift_share_rejects_different_fetch_dates():
    with pytest.raises(GuardViolation) as e:
        shift_share(*segment_numbers({"a": (100, 10)}, MAY), *segment_numbers({"a": (50, 5)}, AUG, as_of=LATER), cfg=CFG)
    assert e.value.guard == 4


def test_shift_share_compares_two_different_windows():
    with pytest.raises(GuardViolation) as e:
        shift_share(*segment_numbers({"a": (100, 10)}, MAY), *segment_numbers({"a": (50, 5)}, MAY), cfg=CFG)
    assert e.value.guard == 4


# Ревью Codex 14.09: доля больше 1 (оплат больше, чем New First SQL в канале) — порог шума не определён, без падения.
def test_noise_of_share_above_one_is_no_data():
    result = noise(ratio(n("sales", 120), n("sales_entry", 100), "c1", CFG))
    assert result.status is Status.NO_DATA and "вне [0; 1]" in result.missing


# Ревью Codex 14.09: требуемое по proxy-месяцам не сильнее proxy.
def test_required_month_keeps_weaker_status_of_inputs():
    months = [n("sales_entry", 658, period=JUL, status=Status.PROXY), n("sales_entry", 630, period=AUG, status=Status.PROXY)]
    assert required_last_month(months, 850, CFG).status is Status.PROXY


# Ревью Codex 14.09: сегменты с разными метриками дают несопоставимые конверсии.
def test_shift_share_rejects_different_metrics_across_segments():
    base_b, events_b = segment_numbers({"a": (100, 10)}, MAY)
    base_a, events_a = segment_numbers({"a": (50, 5)}, AUG)
    base_b.append(replace(n("leads", 80, flow="web", period=MAY), segment="channel=b"))
    events_b.append(replace(n("qualified", 20, flow="web", period=MAY), segment="channel=b"))
    base_a.append(replace(n("leads", 90, flow="web", period=AUG), segment="channel=b"))
    events_a.append(replace(n("qualified", 30, flow="web", period=AUG), segment="channel=b"))
    with pytest.raises(GuardViolation) as e:
        shift_share(base_b, events_b, base_a, events_a, cfg=CFG)
    assert e.value.guard == 4


def test_shift_share_rejects_different_queries():
    base_a, events_a = segment_numbers({"a": (50, 5)}, AUG)
    base_a = [replace(x, source="C:analytics-x:other-query") for x in base_a]
    events_a = [replace(x, source="C:analytics-x:other-query") for x in events_a]
    with pytest.raises(GuardViolation) as e:
        shift_share(*segment_numbers({"a": (100, 10)}, MAY), base_a, events_a, cfg=CFG)
    assert e.value.guard == 4


# Ревью 14.09: маржа — доля денег; рубли знаменателя — не число наблюдений.
def test_noise_of_money_share_rejected():
    margin = money_share(replace(n("gross_profit", 45000000), unit="₽"), replace(n("pnl_revenue", 123000000), unit="₽"),
                         "margin", CFG)
    with pytest.raises(GuardViolation) as e:
        noise(margin)
    assert e.value.guard == 9


# Ревью 14.09: при N·p < 5 нормальное приближение неверно; при p = 0 формула дала бы нулевой шум.
@pytest.mark.parametrize("sales, entries", [(3, 40), (0, 500), (500, 500)])
def test_noise_needs_normal_approximation(sales, entries):
    result = noise(ratio(n("sales", sales), n("sales_entry", entries), "c1", CFG))
    assert result.status is Status.NO_DATA and "неприменимо" in result.missing


def test_noise_at_expected_n_of_test():
    share = ratio(n("sales", 712), n("sales_entry", 1994), "c1", CFG)
    threshold = noise(share, base=600)
    assert threshold.value == pytest.approx(2 * math.sqrt(712 / 1994 * (1 - 712 / 1994) / 600))
    assert threshold.denominator == "ожидаемый N теста=600"
    with pytest.raises(GuardViolation):
        noise(share, base=0)
