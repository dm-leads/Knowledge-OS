"""Команда `source_run`: числа одного источника с разрезом и отбором на лист снимков (29.09.2026)."""
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest
import yaml

from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Number, Status
from growth_engine.core.storage import RegistryStore
from growth_engine.source_run import run
from growth_engine.storage.markdown import MarkdownBackend

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 29)
PAGES = {"/blog/rating-2025-goda": 30.0, "/blog/brizer": 20.0, "/catalog": 50.0,
         "/blog/rating-luxBxAAGIAEMgcICBAAGI8CMgcICRAAGI8C0gEJMzQxNWowajE1qAIIsAIB8QV": 3.0}


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def fetch(self, query):
        self.calls.append(query)
        common = dict(metric=query.metric, level="visit", scope=query.scope, flow=query.flow,
                      period_start=query.period_start, period_end=query.period_end, as_of=query.as_of,
                      source="A:analytics-x:data", status=Status.FACT)
        if query.breakdown:
            return [Number(**common, segment=f"{query.breakdown}={page}", value=v) for page, v in PAGES.items()]
        return [Number(**common, value=sum(PAGES.values()))]


def config(tmp_path) -> str:
    raw = yaml.safe_load((FIXTURES / "config_funnel.yaml").read_text(encoding="utf-8"))
    raw["sources"] = {"analytics-x": {"class": "A"}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return str(path)


def args(tmp_path, **changes):
    base = dict(config=config(tmp_path), secrets="нет", system="analytics-x", metrics="visits",
                scope="p1", flow="web", months="2025-08,2026-08", breakdown="landing_page",
                match=["/blog/*-2025-goda"], out=str(tmp_path / "store"), lark_book=None, google_book=None,
                dry_run=False)
    base.update(changes)
    return Namespace(**base)


def stored(tmp_path):
    return RegistryStore(MarkdownBackend(tmp_path / "store"), allowed_domains=(), today=lambda: TODAY).read("numbers")


def test_writes_totals_and_only_matched_segments(tmp_path):
    lines = []
    assert run(args(tmp_path), out=lines.append, adapter=FakeAdapter(), today=lambda: TODAY) == 0
    numbers = stored(tmp_path)
    assert len(numbers) == 4                       # два итога и по одной отобранной странице на месяц
    assert {x.segment for x in numbers} == {"", "landing_page=/blog/rating-2025-goda"}
    assert "записано 4, прочитано 4" in lines[-1]


def test_breakdown_without_match_is_refused(tmp_path):
    with pytest.raises(GuardViolation, match="--match"):
        run(args(tmp_path, match=None), out=print, adapter=FakeAdapter(), today=lambda: TODAY)


def test_unknown_metric_is_refused(tmp_path):
    with pytest.raises(GuardViolation, match="not_a_metric"):
        run(args(tmp_path, metrics="not_a_metric"), out=print, adapter=FakeAdapter(), today=lambda: TODAY)


def test_unknown_system_names_the_known_ones(tmp_path):
    with pytest.raises(GuardViolation, match="не объявлена в sources"):
        run(args(tmp_path, system="nowhere"), out=print, adapter=FakeAdapter(), today=lambda: TODAY)


def test_dry_run_writes_nothing(tmp_path):
    lines = []
    assert run(args(tmp_path, dry_run=True), out=lines.append, adapter=FakeAdapter(), today=lambda: TODAY) == 0
    assert "ничего не записано" in lines[-1] and not (tmp_path / "store").exists()


def test_segment_that_looks_like_a_secret_is_dropped_by_count(tmp_path):
    """1.2.4: мусор, приклеенный к адресу, не пишется и не останавливает прогон — число исключённых названо."""
    lines = []
    assert run(args(tmp_path, match=["/blog/rating*"]), out=lines.append, adapter=FakeAdapter(),
               today=lambda: TODAY) == 0
    assert any("исключено похожих на секрет или ПДн: 1" in line for line in lines)
    assert all("luxBx" not in line for line in lines)
    assert {x.segment for x in stored(tmp_path)} == {"", "landing_page=/blog/rating-2025-goda"}
