"""Блок «Воронка по каналам» на листе «Воронка и оценка гипотез» (24.09.2026).

Числа — из листа снимков (их пишет funnel_run); блок только показывает их человеку по Красинскому: дошли, конверсия от
первого шага и от прошлого, не прошли. Над каждой частью — подпись: кабинет, поток, источник, дата съёма, статус.
Блок ставится ниже формул владельца и не пишет туда, где лежит чужое.
"""
from datetime import date

import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Number, Status
from growth_engine.funnel_sheet import TITLE, build, free_or_ours, latest_take

MAY, AUG = (date(2026, 5, 1), date(2026, 6, 1)), (date(2026, 8, 1), date(2026, 9, 1))
TAKE = date(2026, 9, 24)
LEVEL = {"visits": "visit", "leads": "lead", "mql": "mql", "new_first_sql": "new_first_sql", "sales": "payment"}


def num(metric, value, period=AUG, flow="web", segment="", take=TAKE, scope="brz"):
    return Number(metric=metric, level=LEVEL[metric], scope=scope, flow=flow, period_start=period[0],
                  period_end=period[1], source="C:roistat:project/analytics/data", status=Status.FACT, value=value,
                  segment=segment, as_of=take)


def funnel(period, visits, leads, mql, nfs, sales, flow="web", segment=""):
    values = {"visits": visits, "leads": leads, "mql": mql, "new_first_sql": nfs, "sales": sales}
    return [num(metric, value, period, flow, segment) for metric, value in values.items()
            if not (metric == "visits" and flow != "web")]


def numbers():
    seo = "marker_level_1=seo"
    direct = "marker_level_1=direct6"
    return (funnel(MAY, 1000, 50, 20, 15, 5) + funnel(AUG, 800, 40, 16, 12, 4)
            + funnel(MAY, 700, 30, 12, 9, 3, segment=seo) + funnel(AUG, 450, 22, 9, 7, 2, segment=seo)
            + funnel(MAY, 300, 20, 8, 6, 2, segment=direct) + funnel(AUG, 350, 18, 7, 5, 2, segment=direct)
            + funnel(MAY, None, 10, 5, 4, 2, flow="no_visit") + funnel(AUG, None, 12, 6, 5, 3, flow="no_visit"))


STAGES = ["visits", "leads", "mql", "new_first_sql", "sales"]


def block():
    return build(numbers(), STAGES, "brz", [MAY[0], AUG[0]])


def row_of(rows, first_cell):
    return next(row for row in rows if row and row[0] == first_cell)


def test_block_starts_with_its_title_and_every_part_is_signed():
    rows, _ = block()
    assert rows[0][0].startswith(TITLE)
    captions = [row[0] for row in rows if row and isinstance(row[0], str) and "снято 24.09.2026" in row[0]]
    assert len(captions) == 3 and all("источник C:roistat" in c and "статус факт" in c for c in captions)


def test_monthly_funnel_counts_people_first_step_previous_step_and_not_passed():
    rows, _ = block()
    leads = row_of(rows, "заявки")
    # май: дошли 50, от первого 5 %, от прошлого 5 %, не прошли 950; август: 40, 5 %, 5 %, 760
    assert leads[1:5] == [50, pytest.approx(0.05), pytest.approx(0.05), 950]
    assert leads[5:9] == [40, pytest.approx(0.05), pytest.approx(0.05), 760]
    sales = row_of(rows, "продажи")
    assert sales[1:5] == [5, pytest.approx(0.005), pytest.approx(5 / 15), 10]


def test_channels_are_sorted_by_first_month_visits_and_show_the_change():
    rows, _ = block()
    seo, direct = row_of(rows, "seo"), row_of(rows, "direct6")
    assert rows.index(seo) < rows.index(direct)
    assert seo[1:4] == [700, 450, -250] and direct[1:4] == [300, 350, 50]
    assert seo[5] == pytest.approx(22 / 450)          # визит → заявка в последнем месяце


def test_no_visit_flow_has_no_visits_row():
    rows, _ = block()
    start = next(i for i, row in enumerate(rows) if row and isinstance(row[0], str) and row[0].startswith("Без визита"))
    part = [row[0] for row in rows[start:] if row]
    assert "визиты" not in part and "заявки" in part


def test_formats_mark_counts_and_shares():
    rows, formats = block()
    kinds = {kind for _, _, _, kind in formats}
    assert kinds == {"count", "share"}
    assert all(0 <= row < len(rows) for row, _, _, _ in formats)


def test_only_the_latest_take_is_shown():
    older = [Number(**{**num("visits", 999).__dict__, "as_of": date(2026, 9, 3)})]
    assert latest_take(numbers() + older) == TAKE
    rows, _ = build(numbers() + older, STAGES, "brz", [MAY[0], AUG[0]])
    assert 999 not in [cell for row in rows for cell in row]


def test_missing_month_stops_rather_than_showing_zeroes():
    with pytest.raises(GuardViolation, match="07.2026") as e:
        build(numbers(), STAGES, "brz", [MAY[0], date(2026, 7, 1)])
    assert e.value.guard == 13


@pytest.mark.parametrize("first_cell, ours", [("", True), (None, True), (TITLE + " · что угодно", True),
                                              ("Моя таблица", False)])
def test_block_area_is_free_or_already_ours(first_cell, ours):
    grid = [[first_cell] + [""] * 5] + [[""] * 6 for _ in range(3)]
    assert free_or_ours(grid) is ours


def test_foreign_cell_inside_the_area_is_not_overwritten():
    grid = [[""] * 6 for _ in range(4)]
    grid[2][3] = "формула владельца"
    assert free_or_ours(grid) is False
