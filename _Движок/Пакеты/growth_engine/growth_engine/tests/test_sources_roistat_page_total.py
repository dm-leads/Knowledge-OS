"""Разрез «страница входа целиком» (1.6.0): сумма маркеров потока по странице, страж Т3 до сложения, хвосты адреса.

Данные условные; раздел конфигурации — минимальный, без имён и чисел инстансов.
"""
from datetime import date

import pytest

from growth_engine.core.config import InstanceConfig, MetricRule
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.sources.base import Query
from growth_engine.sources.roistat import RoistatAdapter

CFG = InstanceConfig(
    goal_metric="leads", goal_window_months=3, goal_baseline=1.0, target_periods=(), business_form={},
    levels=("visit", "lead", "money"), flows=("web", "no_visit", "all"),
    metrics={"visits": MetricRule("visits", "visit", True, False, ""),
             "leads": MetricRule("leads", "lead", True, False, ""),
             "revenue": MetricRule("revenue", "money", True, False, "")},
    storage_adapter="markdown")
SECTION = {"base_url": "https://api.example", "allowed_endpoints": ["project/analytics/data"], "secret_env": ["KEY"],
           "timezone": "+0000", "settings": {}, "currency": "₽", "money_metrics": ["revenue"],
           "scopes": {"a": {"project": "1"}},
           "metric_names": {"visits": {"a": "visits"}, "leads": {"a": "leads"}, "revenue": {"a": "money"}},
           "flow_markers": {"no_visit": {"a": ["crm"]}}}
START, END = date(2026, 8, 1), date(2026, 9, 1)


def body(rows):
    items = [{"dimensions": {"marker_level_1": {"value": m}, "landing_page": {"value": page}},
              "metrics": [{"metric_name": name, "value": v} for name, v in values.items()]}
             for (m, page), values in rows.items()]
    return {"data": [{"items": items}]}


def adapter(rows):
    calls = []

    def transport(method, url, **kwargs):
        calls.append(kwargs)
        return type("Response", (), {"status_code": 200, "json": lambda _self: body(rows)})()

    return RoistatAdapter(SECTION, CFG, {"KEY": "k"}, transport=transport, sleep=lambda s: None), calls


ROWS = {("search", "site.example"): {"leads": 7, "visits": 100, "money": 10},
        ("search", "site.example/?q=1"): {"leads": 1, "visits": 10, "money": 0},
        ("ads", "site.example"): {"leads": 2, "visits": 40, "money": 5},
        ("ads", "site.example/shop"): {"leads": 3, "visits": 50, "money": 7},
        ("", ""): {"leads": 1, "visits": 0, "money": 0},
        ("crm", ""): {"leads": 9, "visits": 0, "money": 20}}


def fetch(rows, metric, flow):
    a, calls = adapter(rows)
    got = a.fetch(Query(metric=metric, scope="a", flow=flow, period_start=START, period_end=END,
                        breakdown="landing_page_total", as_of=date(2026, 9, 2)))
    return {x.segment.partition("=")[2]: x for x in got}, calls


def test_web_flow_sums_markers_by_page():
    got, calls = fetch(ROWS, "leads", "web")
    assert {page: x.value for page, x in got.items()} == {"/": 10, "/shop": 3, "(пусто)": 1}
    assert calls[0]["json"]["dimensions"] == ["marker_level_1", "landing_page"]
    assert all(x.status is Status.FACT for x in got.values())


def test_flows_are_selected_before_the_sum():
    no_visit, _ = fetch(ROWS, "leads", "no_visit")
    everything, _ = fetch(ROWS, "leads", "all")
    assert {page: x.value for page, x in no_visit.items()} == {"(пусто)": 9}
    assert everything["(пусто)"].value == 10 and everything["/"].value == 10


def test_money_by_page_is_proxy():
    got, _ = fetch(ROWS, "revenue", "web")
    assert got["/"].value == 15 and got["/"].status is Status.PROXY and got["/"].missing


def test_visits_of_a_no_visit_marker_stop_before_the_sum():
    rows = {**ROWS, ("crm", "site.example"): {"leads": 0, "visits": 3, "money": 0}}
    with pytest.raises(GuardViolation) as e:
        fetch(rows, "leads", "web")
    assert e.value.guard == 13


@pytest.mark.parametrize("url, page", [("site.example?from=/shop", "/"), ("https://site.example/shop/?a=1", "/shop"),
                                       ("site.example#top", "/"), ("", "")])
def test_page_takes_the_path_before_the_tail(url, page):
    assert RoistatAdapter._page(url) == page
