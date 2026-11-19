"""1.7.6: визиты с любой целью-заявкой и разрез по параметру визита в адаптере веб-аналитики — без сети."""
from datetime import date
from types import SimpleNamespace

import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.sources.base import Query
from growth_engine.sources.metrika import MetrikaAdapter

SECTION = {"base_url": "https://api-metrika.yandex.net", "secret_env": ["TOKEN", "COUNTER"],
           "allowed_endpoints": ["stat/v1/data"], "accuracy": "full", "scopes": {"s": {}}, "breakdown_limit": 1000,
           "junk_page_prefixes": [], "goals": {"form": 1, "quiz": 2, "chat": 3},
           "dead_goals": {"since": "2026-06-16", "ids": [3]}, "lead_goals": ["form", "quiz", "chat"],
           "visit_params": ["ab_h17"]}
RULE = SimpleNamespace(level="visit")
CFG = SimpleNamespace(rule=lambda metric: RULE)


class Recorded:
    def __init__(self, body):
        self.body, self.calls = body, []

    def __call__(self, method, url, **kwargs):
        self.calls.append(kwargs["params"])
        body = self.body
        return type("R", (), {"status_code": 200, "json": lambda _self: body})()


def fetch(metric, breakdown=None, body=None, section=SECTION):
    transport = Recorded(body or {"totals": [143.0], "data": []})
    adapter = MetrikaAdapter(section, CFG, {"TOKEN": "t", "COUNTER": "1"}, transport=transport, sleep=lambda s: None)
    query = Query(metric=metric, scope="s", flow="web", period_start=date(2026, 10, 1),
                  period_end=date(2026, 11, 1), breakdown=breakdown, as_of=date(2026, 11, 19))
    return adapter.fetch(query), transport


def test_lead_visits_are_visits_with_any_live_lead_goal():
    got, t = fetch("lead_visits")
    assert got[0].value == 143.0 and t.calls[0]["metrics"] == "ym:s:visits"
    assert t.calls[0]["filters"] == "(ym:s:goal1IsReached=='Yes' OR ym:s:goal2IsReached=='Yes')"   # мёртвая цель 3 — вне
    assert "не учтены: chat" in got[0].missing


def test_visit_param_breakdown_segments_by_value():
    body = {"data": [{"dimensions": [{"name": "ab_h17"}, {"name": "A"}], "metrics": [900.0]},
                     {"dimensions": [{"name": "ab_h17"}, {"name": "B"}], "metrics": [910.0]},
                     {"dimensions": [{"name": "other"}, {"name": "x"}], "metrics": [50.0]}]}   # чужой параметр визита
    got, t = fetch("site_visits", breakdown="visit_param:ab_h17", body=body)
    assert {x.segment: x.value for x in got} == {"ab_h17=A": 900.0, "ab_h17=B": 910.0}
    assert t.calls[0]["dimensions"] == "ym:s:paramsLevel1,ym:s:paramsLevel2"
    assert t.calls[0]["filters"] == "ym:s:paramsLevel1=='ab_h17'"


def test_lead_visits_by_param_combine_filters():
    _, t = fetch("lead_visits", breakdown="visit_param:ab_h17", body={"data": []})
    assert t.calls[0]["filters"].endswith(" AND ym:s:paramsLevel1=='ab_h17'")


def test_undeclared_param_and_missing_lead_goals_are_refused():
    with pytest.raises(GuardViolation, match="не объявлен"):
        fetch("site_visits", breakdown="visit_param:utm_source")
    with pytest.raises(GuardViolation, match="lead_goals"):
        fetch("lead_visits", section={**SECTION, "lead_goals": []})


def test_dead_goal_counts_before_its_stop_date_and_all_dead_is_no_data():
    early = {**SECTION, "dead_goals": {"since": "2026-12-01", "ids": [3]}}
    _, t = fetch("lead_visits", section=early)
    assert "goal3IsReached" in t.calls[0]["filters"]
    only_dead = {**SECTION, "lead_goals": ["chat"]}
    got, t = fetch("lead_visits", section=only_dead)
    assert got[0].value is None and t.calls == []
