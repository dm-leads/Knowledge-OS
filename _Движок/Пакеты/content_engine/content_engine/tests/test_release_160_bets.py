"""1.6.0, честный замер (аудит 17.11.2026, правка 3): итог ставки по сумме её записей с порогом шума, окно 90 —
предварительное, деньги — только при достаточном числе заявок; правка страницы в окне помечается."""
from content_engine.core import bets, store
from content_engine.core.instance import Instance
from content_engine.steps import bets_cmd, week_plan

from .conftest import TODAY
from .test_bets_wave3 import money_file, record, window

LEADS = lambda value: [{"value": value, "unit": "заявки"}]          # noqa: E731
M = lambda leads, money=1000.0: {"step": 1, "money": {"value": money, "status": "факт"},  # noqa: E731
                                 "leads": None if leads is None else {"value": leads, "status": "факт"}}


def rec(rid="T1", hid="H-1", expected=3, threshold="0", **extra):
    return {"id": rid, "hypothesis_id": hid, "bet_missing": False, "expectation_numbers": LEADS(expected),
            "fail_threshold": threshold, **extra}


def test_group_of_one_hypothesis_decides_on_sum():
    got, why = bets.decide_group([(rec("T1"), M(4)), (rec("T2"), M(3))], 2, "180")
    assert got == bets.PLAYED and "по 2 статьям" in why            # E = 6 ≥ 5, g = 7, отрыв 7 > шума ±4,9


def test_single_small_bet_is_low_data_below_noise():
    got, why = bets.decide(rec(expected=2), M(3), 2, "180")
    assert got == bets.LOW_DATA and "ниже порога шума" in why


def test_played_needs_gap_from_threshold_above_noise():
    got, why = bets.decide(rec(expected=20, threshold="15 заявок"), M(21), 2, "180")
    assert got == bets.LOW_DATA and "в пределах шума" in why       # 21 − 15 = 6 < 2·√20 ≈ 8,9


def test_fail_only_at_180_and_window_90_preliminary():
    r = rec(expected=20, threshold="2 заявки")
    assert bets.decide(r, M(1), 2, "180")[0] == bets.FAILED
    got, why = bets.decide(r, M(0), 2, "90")
    assert got == bets.LOW_DATA and "на 180" in why


def test_fail_within_noise_is_low_data():
    got, why = bets.decide(rec(expected=6, threshold="2 заявки"), M(2), 2, "180")
    assert got == bets.LOW_DATA and "в пределах шума" in why       # недобор 4 < 2·√6 ≈ 4,9


def test_money_bet_needs_five_leads_and_names_missing_leads():
    r = {"id": "T1", "bet_missing": False, "expectation_numbers": [{"value": 50000, "unit": "₽"}],
         "fail_threshold": "0"}
    got, why = bets.decide(r, M(3, 90000), 2, "180")
    assert got == bets.LOW_DATA and "меньше 5" in why
    got, why = bets.decide(r, M(None, 90000), 2, "180")
    assert got == bets.LOW_DATA and "нет заявок" in why
    assert bets.decide(r, M(6, 90000), 2, "180")[0] == bets.PLAYED


def test_measure_proposes_group_result_to_each_record_and_marks_90(project):
    instance = Instance(project, today=TODAY)
    a = record("T0001", published="2025-08-01", expectation_numbers=LEADS(4))
    b = record("T0002", published="2025-08-01", url="example.ru/blog/gazon-vesnoj", expectation_numbers=LEADS(4))
    store.write_json(instance.file("bets"), "bets", [a, b])
    f, t = window("2025-08-01", 180)
    money_file(project, [("otoplenie-teplicy", 30000, 6, f, t), ("gazon-vesnoj", 20000, 5, f, t)])
    bets_cmd.measure(Instance(project, today=TODAY))
    rows = bets.load(instance)
    assert [r["measures"]["180"]["proposed"] for r in rows] == [bets.PLAYED, bets.PLAYED]   # E = 8, g = 11
    assert "по 2 статьям" in rows[0]["measures"]["180"]["why"]
    assert rows[0]["measures"]["90"].get("preliminary") is True
    plan = week_plan.plan(Instance(project, today=TODAY))
    assert any("bet accept" in task["what"] for task in plan["tasks"])


def test_refresh_inside_window_is_marked(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [record(refreshed=[{"day": "2025-11-15", "id": "refresh-x"}])])
    money_file(project, [("otoplenie-teplicy", 30000, 12, *window("2025-10-01", 90))])
    bets_cmd.measure(Instance(project, today=TODAY))
    m = bets.load(instance)[0]["measures"]["90"]
    assert m["refreshed_in_window"] == ["2025-11-15"] and "в окне правка 2025-11-15" in m["why"]


def test_group_with_unmeasured_member_is_low_data():
    """Проверка Codex 18.11.2026: запись ставки с наступившим окном без замера — итог по неполной ставке не выносится."""
    from datetime import date
    a = {**rec("T1", expected=10), "published": "2025-01-01", "measures": {"180": {**M(12), "result": None}}}
    b = {**rec("T2", expected=100), "published": "2025-01-01", "measures": {}}
    bets_cmd._propose_groups([a, b], 2, date(2025, 12, 1))
    assert a["measures"]["180"]["proposed"] == bets.LOW_DATA and "замера нет у T2" in a["measures"]["180"]["why"]
