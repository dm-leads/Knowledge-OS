import pytest

from growth_engine.core.arithmetic import add
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.tests.helpers import AS_OF, AUG, CFG, JUL, JUN, LATER, n


# Известный ответ: New First SQL, август 2026 = 587 + 43 = 630 (снимок unit_econ_funnel, 03.09).
def test_sales_entry_two_scopes_by_declared_exception():
    total = add([n("sales_entry", 587, "p1"), n("sales_entry", 43, "p2")], CFG)
    assert (total.value, total.scope, total.status) == (630, "p1+p2", Status.FACT)


# Заявки двух кабинетов, август 2026: 2 227 + 298 — сквозной дедуп не доказан (П1).
def test_leads_two_scopes_rejected():
    with pytest.raises(GuardViolation) as e:
        add([n("leads", 2227, "p1"), n("leads", 298, "p2")], CFG)
    assert e.value.guard == 3


def test_different_levels_rejected():
    with pytest.raises(GuardViolation) as e:
        add([n("qualified", 777), n("sales_entry", 587)], CFG)
    assert e.value.guard == 3


# Уровень числа обязан совпадать с объявленным в конфигурации — иначе ступень подменена.
def test_level_must_match_config():
    from dataclasses import replace
    wrong = [replace(n("sales_entry", 587, "p1"), level="lead"), replace(n("sales_entry", 43, "p2"), level="lead")]
    with pytest.raises(GuardViolation, match="объявлен"):
        add(wrong, CFG)


# Известный ответ: New First SQL, июн–авг 2026 = 706 + 658 + 630 = 1 994 (модель v1.0).
def test_months_sum_when_period_additive():
    total = add([n("sales_entry", 706, period=JUN), n("sales_entry", 658, period=JUL),
                 n("sales_entry", 630, period=AUG)], CFG)
    assert total.value == 1994
    assert (total.period_start, total.period_end) == (JUN[0], AUG[1])


# Страж 3: окна метрики с флагом period_additive: false в конфигурации не складываются (в фикстуре — sales).
def test_months_rejected_when_not_period_additive():
    with pytest.raises(GuardViolation):
        add([n("sales", 134, period=JUL), n("sales", 118, period=AUG)], CFG)


def test_gap_between_windows_rejected():
    with pytest.raises(GuardViolation):
        add([n("sales_entry", 706, period=JUN), n("sales_entry", 630, period=AUG)], CFG)


def test_incomplete_grid_rejected():
    with pytest.raises(GuardViolation):
        add([n("sales_entry", 621, "p1", period=JUL), n("sales_entry", 43, "p2", period=AUG)], CFG)


def test_duplicate_rejected():
    with pytest.raises(GuardViolation):
        add([n("sales_entry", 587), n("sales_entry", 587)], CFG)


def test_different_flows_rejected():
    with pytest.raises(GuardViolation):
        add([n("leads", 1051, flow="web"), n("leads", 1474, flow="no_visit")], CFG)


def test_different_source_systems_rejected():
    with pytest.raises(GuardViolation, match="систем"):
        add([n("visits", 100, period=JUL), n("visits", 50, period=AUG, source="A:web-analytics-y:data")], CFG)


def test_different_source_classes_rejected():
    with pytest.raises(GuardViolation, match="классов"):
        add([n("visits", 100, period=JUL), n("visits", 50, period=AUG, source="A:analytics-x:data")], CFG)


def seg(value, marker, scope="p1"):
    from dataclasses import replace
    return replace(n("leads", value, scope), segment=f"marker_level_1={marker}")


# Разрез одного измерения складывается и помечается как сумма значений, а не как итог.
def test_segments_of_one_dimension_sum_with_label():
    total = add([seg(1981, "seo"), seg(316, "direct6"), seg(3427, "nosource-crm")], CFG)
    assert total.value == 5724 and total.segment == "marker_level_1=сумма 3 значений"


def test_total_with_its_part_is_double_count():
    with pytest.raises(GuardViolation) as e:
        add([n("leads", 7188), seg(1981, "seo")], CFG)
    assert e.value.guard == 3


def test_segments_of_different_dimensions_rejected():
    from dataclasses import replace
    page = replace(n("leads", 50), segment="landing_page=/catalog/")
    with pytest.raises(GuardViolation):
        add([seg(1981, "seo"), page], CFG)


# Среднее (позиция, чек) не складывается ни по окнам, ни по разрезам.
def test_non_summable_metric_rejected():
    from dataclasses import replace
    first = replace(n("avg_check", 120000.0), segment="query=бризер")
    second = replace(n("avg_check", 90000.0), segment="query=кондиционер")
    with pytest.raises(GuardViolation, match="не складывается"):
        add([first, second], CFG)


# Оговорки слагаемых не теряются в сумме — иначе предупреждение исчезает молча.
def test_sum_keeps_missing_notes_of_items():
    from dataclasses import replace
    calls = n("sales_entry", 386, "p1")
    chats = replace(n("sales_entry", 161, "p2"), missing="цитата может лежать в соседней беседе")
    assert "соседней беседе" in add([calls, chats], CFG).missing


def test_no_data_propagates():
    total = add([n("sales_entry", 587, "p1"), n("sales_entry", None, "p2", status=Status.NO_DATA)], CFG)
    assert total.status is Status.NO_DATA and total.value is None


def test_weakest_status_wins():
    total = add([n("sales_entry", 587, "p1"), n("sales_entry", 43, "p2", status=Status.ESTIMATE)], CFG)
    assert total.status is Status.ESTIMATE and total.value == 630


# П3: июнь снят 03.09, июль — 14.09: окна складываются, а разные съёмы — нет.
def test_windows_of_different_fetch_dates_rejected():
    with pytest.raises(GuardViolation) as e:
        add([n("sales_entry", 706, period=JUN), n("sales_entry", 658, period=JUL, as_of=LATER)], CFG)
    assert e.value.guard == 3 and "съём" in str(e.value)


def test_sum_carries_fetch_date():
    assert add([n("sales_entry", 587, "p1"), n("sales_entry", 43, "p2")], CFG).as_of == AS_OF


# П3 (ревью Codex 14.09): одинаковая дата съёма не спасает числа разных запросов одной системы.
def test_numbers_of_different_queries_are_not_summed():
    with pytest.raises(GuardViolation) as e:
        add([n("sales_entry", 706, period=JUN), n("sales_entry", 658, period=JUL, source="C:analytics-x:other-query")], CFG)
    assert e.value.guard == 3
