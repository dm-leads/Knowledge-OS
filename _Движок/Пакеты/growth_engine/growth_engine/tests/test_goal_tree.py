"""Команда `goal_tree`: цель окна и потолки веток дерева цели числами модели (28.09.2026).

Известный ответ собран руками на поддельном адаптере: два кабинета, три месяца, ветки «поиск A», «поиск B», «прямые»,
«остальное» веб-потока и «без визита», плюс рычаг конверсии. Потолок ветки-потока — лучший месяц суммы кабинетов;
прирост рычага — веб-визиты последнего месяца × (лучшая конверсия − последняя) по каждому кабинету.
"""
import json
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest
import yaml

from growth_engine.core.artifacts import GoalTree, Branch
from growth_engine.core.config import parse_config
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Number, Status
from growth_engine.core.storage import RegistryStore, number_id
from growth_engine.goal_tree import assign, parse_branches, run
from growth_engine.registry import run as registry_run
from growth_engine.storage.markdown import MarkdownBackend

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 28)
MONTHS = [date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)]

BRANCHES = [
    {"id": "B1", "name": "поиск A", "flow": "web", "markers": ["seo › a"]},
    {"id": "B2", "name": "поиск B", "flow": "web", "markers": ["seo › b"]},
    {"id": "B3", "name": "прямые", "flow": "web", "markers": ["(пусто) › *"]},
    {"id": "B4", "name": "прочие веб", "flow": "web", "rest": True},
    {"id": "B5", "name": "без визита", "flow": "no_visit", "rest": True},
    {"id": "B6", "name": "конверсия сайта", "lever": "conversion"},
]

# (кабинет, месяц) → поток → значение разреза → единицы цели; визиты веб-потока — отдельно.
DATA = {
    ("p1", MONTHS[0]): {"web": {"seo › a": 30, "seo › b": 20, "(пусто) › (пусто)": 10, "site › x.ru": 2},
                        "no_visit": {"crm › (пусто)": 40}},
    ("p1", MONTHS[1]): {"web": {"seo › a": 25, "seo › b": 22, "(пусто) › (пусто)": 12, "site › x.ru": 1},
                        "no_visit": {"crm › (пусто)": 35}},
    ("p1", MONTHS[2]): {"web": {"seo › a": 20, "seo › b": 24, "(пусто) › (пусто)": 8, "site › x.ru": 0},
                        "no_visit": {"crm › (пусто)": 30}},
    ("p2", MONTHS[0]): {"web": {"seo › a": 3, "seo › b": 2}, "no_visit": {"crm › (пусто)": 5}},
    ("p2", MONTHS[1]): {"web": {"seo › a": 2, "seo › b": 2}, "no_visit": {"crm › (пусто)": 4}},
    ("p2", MONTHS[2]): {"web": {"seo › a": 1, "seo › b": 3}, "no_visit": {"crm › (пусто)": 6}},
}
VISITS = {("p1", MONTHS[0]): 6200, ("p1", MONTHS[1]): 6000, ("p1", MONTHS[2]): 5200,
          ("p2", MONTHS[0]): 500, ("p2", MONTHS[1]): 400, ("p2", MONTHS[2]): 400}


class FakeAdapter:
    def __init__(self, data=DATA, total_shift=0):
        self.data, self.total_shift, self.calls = data, total_shift, 0

    def fetch(self, query):
        self.calls += 1
        cell = self.data[(query.scope, query.period_start)]
        common = dict(level="visit" if query.metric == "visits" else "sales_entry", scope=query.scope,
                      flow=query.flow, period_start=query.period_start, period_end=query.period_end,
                      as_of=query.as_of, source="C:analytics-x:data", status=Status.FACT)
        if query.metric == "visits":
            return [Number(metric="visits", value=float(VISITS[(query.scope, query.period_start)]), **common)]
        flows = ("web", "no_visit") if query.flow == "all" else (query.flow,)
        if query.breakdown:
            return [Number(metric=query.metric, segment=f"{query.breakdown}={value}", value=float(n), **common)
                    for flow in flows for value, n in cell[flow].items()]
        total = sum(n for flow in flows for n in cell[flow].values())
        return [Number(metric=query.metric, value=float(total + (self.total_shift if query.flow == "all" else 0)),
                       **common)]


def config(tmp_path, **changes) -> str:
    raw = yaml.safe_load((FIXTURES / "config_minimal.yaml").read_text(encoding="utf-8"))
    raw["goal_tree"] = {"system": "analytics-x", "breakdown": "marker_level_2", "target": 150,
                        "target_note": "цель владельца — тест", "scope": "p1+p2",
                        "history": {"from": "2026-06", "to": "2026-08"}, "branches": BRANCHES, **changes}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return str(path)


def call(tmp_path, adapter=None, dry_run=False, **changes):
    args = Namespace(config=config(tmp_path, **changes), secrets="нет", month="2026-10",
                     out=None if dry_run else str(tmp_path / "store"), lark_book=None, google_book=None,
                     dry_run=dry_run)
    lines = []
    code = run(args, out=lines.append, adapter=adapter or FakeAdapter(), today=lambda: TODAY)
    return code, lines


def store(tmp_path) -> RegistryStore:
    return RegistryStore(MarkdownBackend(tmp_path / "store"), allowed_domains=(), today=lambda: TODAY)


def ceilings(tmp_path) -> dict:
    return {x.segment.partition("=")[2]: x for x in store(tmp_path).read("numbers")
            if "потолок ветки" in x.source}


def test_ceiling_of_a_flow_branch_is_its_best_month_across_scopes(tmp_path):
    """B1: 33, 27, 21 по месяцам суммой кабинетов → потолок 33 (06.2026), оценка, система «модель»."""
    code, lines = call(tmp_path)
    assert code == 0, lines[-1]
    b1 = ceilings(tmp_path)["B1"]
    assert b1.value == 33 and b1.status is Status.ESTIMATE and b1.source_system == "модель"
    assert b1.scope == "p1+p2" and b1.period_start == date(2026, 10, 1) and "06.2026 = 33" in b1.missing


def test_rest_branch_takes_what_no_pattern_matched(tmp_path):
    call(tmp_path)
    assert ceilings(tmp_path)["B4"].value == 2        # site › x.ru: 2, 1, 0


def test_conversion_lever_is_an_uplift_at_last_month_traffic(tmp_path):
    """p1: конверсия 62/6200 = 1% (06), 60/6000 = 1% (07), 52/5200 = 1% (08) — прироста нет;
    p2: 5/500 = 1% (06), 4/400 = 1% (07), 4/400 = 1% (08) — прироста нет. Сдвинем август p1: 42/5200."""
    data = dict(DATA)
    data[("p1", MONTHS[2])] = {"web": {"seo › a": 10, "seo › b": 24, "(пусто) › (пусто)": 8, "site › x.ru": 0},
                               "no_visit": {"crm › (пусто)": 30}}
    code, lines = call(tmp_path, adapter=FakeAdapter(data))
    assert code == 0, lines[-1]
    lever = ceilings(tmp_path)["B6"]
    # 5 200 × (1% − 42/5200) = 52 − 42 = 10
    assert abs(lever.value - 10) < 1e-9 and "прирост" in lever.missing and lever.flow == "web"


def test_goal_is_a_model_estimate_in_the_target_month(tmp_path):
    call(tmp_path)
    goal, = [x for x in store(tmp_path).read("numbers") if "цель окна" in x.source]
    assert goal.value == 150 and goal.status is Status.ESTIMATE and goal.flow == "all"
    assert goal.period_start == date(2026, 10, 1) and goal.missing == "цель владельца — тест"


def test_printed_ids_build_a_tree_the_registry_accepts(tmp_path):
    """Строка «для tree --json» подаётся подкоманде tree как есть: цель и потолки сходятся по стражу 3."""
    _, lines = call(tmp_path)
    ready = next(line for line in lines if line.startswith("для tree --json: "))
    args = Namespace(cmd="tree", config=str(FIXTURES / "config_minimal.yaml"), out=str(tmp_path / "store"),
                     lark_book=None, google_book=None, dry_run=False, cycle="", id=None, status=None,
                     json=ready.partition(": ")[2], secrets=None, source=None)
    printed = []
    assert registry_run(args, out=printed.append, today=lambda: TODAY) == 0, printed[-1]


def test_branch_facts_are_written_per_scope_and_summed(tmp_path):
    call(tmp_path)
    numbers = store(tmp_path).read("numbers")
    b1 = [x for x in numbers if x.segment == "ветка дерева цели=B1" and x.source_system != "модель"]
    assert {x.scope for x in b1} == {"p1", "p2", "p1+p2"}
    assert all(x.status is Status.FACT for x in b1)


def test_totals_that_do_not_split_into_branches_stop(tmp_path):
    with pytest.raises(GuardViolation, match="без остатка"):
        call(tmp_path, adapter=FakeAdapter(total_shift=1))


def test_a_value_outside_every_branch_stops_without_rest():
    specs = parse_branches({"branches": [{"id": "B1", "name": "поиск", "flow": "web", "markers": ["seo › *"]}]},
                           ("web", "no_visit", "all"))
    segment = Number(metric="sales_entry", level="sales_entry", scope="p1", flow="web", segment="m=site › x.ru",
                     period_start=MONTHS[0], period_end=MONTHS[1], as_of=TODAY, source="C:analytics-x:data",
                     status=Status.FACT, value=1.0)
    with pytest.raises(GuardViolation, match="site › x.ru"):
        assign([segment], specs, "web")


def test_overlapping_branches_stop():
    specs = parse_branches({"branches": [{"id": "B1", "name": "a", "flow": "web", "markers": ["seo › *"]},
                                         {"id": "B2", "name": "b", "flow": "web", "markers": ["seo › a"]}]},
                           ("web", "no_visit", "all"))
    segment = Number(metric="sales_entry", level="sales_entry", scope="p1", flow="web", segment="m=seo › a",
                     period_start=MONTHS[0], period_end=MONTHS[1], as_of=TODAY, source="C:analytics-x:data",
                     status=Status.FACT, value=1.0)
    with pytest.raises(GuardViolation, match="не пересекаться"):
        assign([segment], specs, "web")


@pytest.mark.parametrize("branches, text", [
    ([{"id": "B1", "name": "a", "flow": "web", "markers": ["x"]}, {"id": "B1", "name": "b", "flow": "web", "rest": True}],
     "дважды"),
    ([{"id": "B1", "name": "a", "flow": "offline", "markers": ["x"]}], "не объявлен"),
    ([{"id": "B1", "name": "a", "flow": "web"}], "markers"),
    ([{"id": "B1", "name": "a", "lever": "price"}], "рычаг"),
])
def test_bad_branch_config_is_guard_9(branches, text):
    with pytest.raises(GuardViolation, match=text):
        parse_branches({"branches": branches}, ("web", "no_visit", "all"))


def test_dry_run_writes_nothing(tmp_path):
    code, lines = call(tmp_path, dry_run=True)
    assert code == 0 and "ничего не записано" in lines[-1]
    assert not (tmp_path / "store").exists()


def test_config_without_section_is_guard_9(tmp_path):
    lines = []
    args = Namespace(config=str(FIXTURES / "config_minimal.yaml"), secrets="нет", month="2026-10",
                     out=str(tmp_path), lark_book=None, google_book=None, dry_run=False)
    with pytest.raises(GuardViolation, match="goal_tree"):
        run(args, out=lines.append, adapter=FakeAdapter(), today=lambda: TODAY)
