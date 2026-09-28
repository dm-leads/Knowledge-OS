"""Движок экономики: сверка систем (мост П2), деньги на единицу, маржа, тождество PnL, дозревание окна, денежная модель."""
from dataclasses import replace

import pytest

from growth_engine.core.economy import (Tolerance, check_pnl, mature, money_model, money_share, per_unit, reconcile,
                                        require_reconciled)
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.tests.helpers import AUG, CFG, JUL, LATER, n

ANALYTICS, CRM = "C:analytics-x:data", "D:crm-y:leads"
TIGHT = Tolerance(abs_units=3, rel=0.01)


def pair(left, right, **kw):
    return n("sales_entry", left, source=ANALYTICS, **kw), n("sales_entry", right, source=CRM, **kw)


# Известный ответ 10.09.2026: 26.08–08.09 — сквозная аналитика 264, зеркало CRM 264.
def test_same_count_in_two_systems_passes():
    result = reconcile(*pair(264, 264), TIGHT)
    assert result.ok and result.diff == 0 and "analytics-x 264 против crm-y 264" in result.note


# Август 2026, Бризекс: 587 без фильтров против 546 с фильтрами отчёта — это разные величины, сверка их ловит.
def test_out_of_tolerance_stops_with_guard_2():
    result = reconcile(*pair(587, 546), TIGHT)
    assert not result.ok and result.diff == -41
    with pytest.raises(GuardViolation) as e:
        require_reconciled([result])
    assert e.value.guard == 2 and "вне допуска" in str(e.value)


# Малый кабинет: 43 против 45 — 4,7%, но 2 сделки; стоп только когда превышены оба порога.
def test_small_count_passes_by_absolute_threshold():
    assert reconcile(*pair(43, 45), TIGHT).ok
    assert not reconcile(*pair(43, 47), TIGHT).ok


def test_same_system_is_not_a_bridge():
    with pytest.raises(GuardViolation) as e:
        reconcile(n("sales_entry", 264, source=ANALYTICS), n("sales_entry", 264, source=ANALYTICS), TIGHT)
    assert e.value.guard == 4


@pytest.mark.parametrize("right_kw", [{"period": JUL}, {"scope": "p2"}, {"flow": "web"}])
def test_different_quantities_are_not_reconciled(right_kw):
    left = n("sales_entry", 264, source=ANALYTICS, period=AUG)
    with pytest.raises(GuardViolation) as e:
        reconcile(left, n("sales_entry", 264, source=CRM, **{"period": AUG, **right_kw}), TIGHT)
    assert e.value.guard == 4


# П3: сверка — явное сравнение, поэтому разные съёмы допустимы, но только в объявленных пределах.
def test_fetch_gap_only_within_declared_days():
    left, right = n("sales_entry", 264, source=ANALYTICS), n("sales_entry", 264, source=CRM, as_of=LATER)
    with pytest.raises(GuardViolation) as e:
        reconcile(left, right, TIGHT)
    assert e.value.guard == 4 and "съём" in str(e.value)
    assert reconcile(left, right, TIGHT, max_fetch_gap_days=11).ok


def test_no_data_is_not_reconciled():
    left, right = n("sales_entry", 264, source=ANALYTICS), n("sales_entry", None, source=CRM, status=Status.NO_DATA)
    result = reconcile(left, right, TIGHT)
    assert not result.ok and result.diff is None and "нет данных" in result.note


def test_tolerance_must_be_non_negative():
    with pytest.raises(ValueError):
        Tolerance(abs_units=-1, rel=0.01)


def rub(metric, value, **kw):
    return replace(n(metric, value, **kw), unit="₽")


# Кабинет, июн–авг 2026 (снимок 03.09, Бризекс): New First SQL 1 860, оплат 554, выручка, себестоимость и прибыль PnL.
def cabinet(**overrides):
    inputs = dict(users=n("sales_entry", 1860), sales=n("sales", 554), revenue=rub("pnl_revenue", 67_619_396),
                  cogs=rub("pnl_cogs", 43_039_139), profit=rub("gross_profit", 24_580_257))
    return {**inputs, **overrides}


# П2: вся цепочка — в одной системе, окне и съёме; AMPU = C1 × чек × маржа выполняется по построению.
def test_money_model_of_one_system():
    m = money_model(**cabinet(), cfg=CFG)
    assert m.ampu.value == pytest.approx(m.c1.value * m.avg_check.value * m.margin.value)
    assert m.ampu.value == pytest.approx(24_580_257 / 1860) and m.amppu.value == pytest.approx(24_580_257 / 554)
    assert (m.avg_check.unit, m.ampu.unit, m.margin.unit) == ("₽ на sales", "₽ на sales_entry", "доля")
    assert m.c1.denominator == "sales_entry=1860" and m.ampu.denominator == "sales_entry=1860"


# Страж 2 и ворота этапа: подмена одного входного числа — выручка − себестоимость ≠ прибыль — роняет модель.
def test_substituted_input_breaks_pnl_cross_check():
    with pytest.raises(GuardViolation) as e:
        money_model(**cabinet(cogs=rub("pnl_cogs", 43_039_139 + 1_000)), cfg=CFG)
    assert e.value.guard == 2 and "себестоимость" in str(e.value)


def test_pnl_identity_within_one_rouble_passes():
    check_pnl(rub("pnl_revenue", 100.0), rub("pnl_cogs", 60.4), rub("gross_profit", 40.0))


def test_pnl_identity_without_data_stops():
    with pytest.raises(GuardViolation) as e:
        check_pnl(rub("pnl_revenue", 100.0), rub("pnl_cogs", None, status=Status.NO_DATA), rub("gross_profit", 40.0))
    assert e.value.guard == 2


# П3: деньги и счётчик разных съёмов не делятся.
def test_money_per_unit_needs_same_fetch():
    with pytest.raises(GuardViolation) as e:
        per_unit(rub("gross_profit", 1000), n("sales_entry", 10, as_of=LATER), "ampu", CFG)
    assert e.value.guard == 4 and "съём" in str(e.value)


# П2: деньги одной системы не делятся на счётчик другой.
def test_money_per_unit_needs_same_system():
    with pytest.raises(GuardViolation) as e:
        per_unit(rub("gross_profit", 1000), n("sales_entry", 10, source=CRM), "ampu", CFG)
    assert e.value.guard == 4


def test_money_is_divided_only_by_count():
    with pytest.raises(GuardViolation) as e:
        per_unit(rub("pnl_revenue", 1000), rub("gross_profit", 400), "revenue_per_profit", CFG)
    assert e.value.guard == 4


def test_count_is_not_money():
    with pytest.raises(GuardViolation) as e:
        per_unit(n("sales", 5), n("sales_entry", 10), "sales_per_entry", CFG)
    assert e.value.guard == 4


def test_zero_count_gives_no_data_not_infinity():
    result = per_unit(rub("gross_profit", 1000), n("sales_entry", 0), "ampu", CFG)
    assert result.status is Status.NO_DATA and result.value is None


def test_margin_only_from_money_of_one_currency():
    with pytest.raises(GuardViolation) as e:
        money_share(rub("gross_profit", 400), n("sales_entry", 10), "margin", CFG)
    assert e.value.guard == 4


# Деньги по каналу зависят от атрибуции — «деньги на единицу» канала остаются proxy.
def test_money_per_unit_of_channel_stays_proxy():
    segment = "marker_level_1=direct6"
    profit = replace(rub("gross_profit", 502_915, status=Status.PROXY), segment=segment)
    result = per_unit(profit, replace(n("sales_entry", 139), segment=segment), "profit_per_entry", CFG)
    assert result.status is Status.PROXY and result.segment == segment


# Т6: окно моложе цикла сделки дозревает — «оценка» с датой; зрелое окно не трогается.
def test_young_window_matures_with_date():
    young = mature(rub("gross_profit", 1000), horizon_days=180)
    assert young.status is Status.ESTIMATE and "дозревает до 28.02.2027" in young.missing
    assert mature(rub("gross_profit", 1000), horizon_days=1).status is Status.FACT


# Дозревание касается денег и оплат, а не входа: New First SQL окна задним числом не меняется.
def test_money_model_matures_money_not_users():
    cfg = replace(CFG, cohort_horizon_days=180, cohort_horizon_proof="тест")
    m = money_model(**cabinet(), cfg=cfg)
    assert m.users.status is Status.FACT and m.profit.status is Status.ESTIMATE and m.ampu.status is Status.ESTIMATE


# П3 (ревью Codex 14.09): деньги и счётчик разных запросов одной системы не делятся.
def test_money_per_unit_needs_same_query():
    with pytest.raises(GuardViolation) as e:
        per_unit(rub("gross_profit", 1000), n("sales_entry", 10, source="C:analytics-x:other-query"), "ampu", CFG)
    assert e.value.guard == 4
