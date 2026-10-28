"""Вердикт дня, панель и сводка (модуль Д1, Т34.3–Т34.5, П50, П51)."""
import json
from datetime import timedelta

import pytest

from content_engine.cli import main
from content_engine.core import audit_store, publications
from content_engine.core.instance import Instance
from content_engine.core.verdict import decide
from content_engine.steps import pulse

from .conftest import TODAY, set_profile
from .keysso_transport import Response

GOOD = {"site_ok": True, "site_why": "", "last_publication": TODAY.isoformat(), "drift_critical": 0,
        "audit_critical": 0, "audit_age": 3, "stock_days": 10}


@pytest.mark.parametrize("change, color, rule", [
    ({}, "зелёный", 6),
    ({"site_ok": False, "site_why": "HTTP 500"}, "красный", 1),
    ({"last_publication": (TODAY - timedelta(days=5)).isoformat()}, "красный", 2),
    ({"drift_critical": 1}, "красный", 3),
    ({"audit_critical": 2}, "красный", 3),
    ({"stock_days": 2}, "жёлтый", 4),
    ({"audit_age": 40}, "жёлтый", 5),
    ({"audit_age": None}, "жёлтый", 5),
    ({"site_ok": False, "stock_days": 1}, "красный", 1),           # первое сработавшее правило
])
def test_verdict_rules_first_match(project, change, color, rule):
    set_profile(project, lambda d: d["queue"].update(per_day=1))
    got = decide(Instance(project, today=TODAY), {**GOOD, **change})
    assert (got["color"], got["rule"]) == (color, rule), got


def test_silence_depends_on_rhythm(project):
    two_days = {**GOOD, "last_publication": (TODAY - timedelta(days=2)).isoformat()}
    set_profile(project, lambda d: d["queue"].update(per_day=0.5))        # раз в два дня: порог 4 дня
    assert decide(Instance(project, today=TODAY), two_days)["color"] == "зелёный"
    set_profile(project, lambda d: d["queue"].update(per_day=1))          # раз в день: порог 2 дня — уже не красный
    assert decide(Instance(project, today=TODAY), two_days)["color"] == "зелёный"
    three = {**GOOD, "last_publication": (TODAY - timedelta(days=3)).isoformat()}
    assert decide(Instance(project, today=TODAY), three)["color"] == "красный"


@pytest.fixture
def at_today(monkeypatch):
    """Команды через main() — с датой теста, а не с сегодняшней."""
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, **{"today": TODAY, **kw}))


def test_panel_not_connected_not_zero(project, at_today, capsys):
    assert main(["pulse", "run", "--no-network", "--profile", str(project)]) == 0
    instance = Instance(project, today=TODAY)
    page = (instance.reports_dir() / "panel.html").read_text(encoding="utf-8")
    assert "не проверялось" in page and "аудита нет" in page
    assert "<!--" not in page
    data = json.loads((project.parent / "data" / "pulse" / f"{TODAY.isoformat()}.json").read_text(encoding="utf-8"))
    assert data["blocks"]["drift"]["critical"] is None                     # не проверялось — None, а не 0
    assert "Цена статей" in page and data["verdict"]["color"] in ("жёлтый", "красный", "зелёный")


def test_pulse_reads_audit_snapshot(project):
    instance = Instance(project, today=TODAY)
    audit_store.write(instance, "full", 1, [{"id": "title_empty|x", "url": "x", "check": "title_empty",
                                             "level": "critical", "detail": "", "first_seen": TODAY.isoformat()}])
    data = pulse.collect(instance, network=False)
    assert data["blocks"]["audit"]["critical"] == 1 and data["verdict"]["rule"] in (2, 3)


def test_diff_only_changes(project):
    old = {"verdict": {"color": "зелёный", "why": ""}, "blocks": {
        "positions": {"alarms": []}, "drift": {"findings": []}, "stock": {"days": 10},
        "publications": {"last_day": "2026-02-28"}}}
    new = {"verdict": {"color": "жёлтый", "why": "запас"}, "blocks": {
        "positions": {"alarms": []}, "drift": {"findings": []}, "stock": {"days": 2},
        "publications": {"last_day": "2026-02-28"}}}
    lines = pulse.diff(old, new)
    assert lines == ["вердикт: зелёный → жёлтый — запас", "запас: 10 → 2 дн."]
    assert pulse.diff(old, {**old}) == []


class Lark:
    def __init__(self, code=0):
        self.code, self.calls = code, 0

    def __call__(self, method, url, params=None, json=None, data=None, headers=None, timeout=None, **kw):
        self.calls += 1
        return Response(200, {"code": self.code})


@pytest.fixture
def lark(project, monkeypatch):
    monkeypatch.setenv("NOTIFY_LARK_WEBHOOK", "https://open.larksuite.example/open-apis/bot/v2/hook/abc")
    set_profile(project, lambda d: d["notify"].update(kind="lark_webhook", url_env="NOTIFY_LARK_WEBHOOK"))
    site = Lark()
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, today=TODAY, transport=site, **kw))
    assert main(["pulse", "run", "--no-network", "--profile", str(project)]) == 0
    return site


def test_notify_needs_right_and_apply(project, lark, capsys):
    assert main(["pulse", "notify", "--profile", str(project)]) == 0            # без --apply — план
    assert "план" in capsys.readouterr().out and lark.calls == 0
    assert main(["pulse", "notify", "--apply", "--profile", str(project)]) == 1   # ступень files
    assert lark.calls == 0


def test_notify_confirmed_by_code_and_once_a_day(project, lark, capsys):
    set_profile(project, lambda d: d["notify"].update(allow="write"))
    assert main(["pulse", "notify", "--apply", "--profile", str(project)]) == 0
    assert lark.calls == 1 and "записано 1 — подтверждено 1" in capsys.readouterr().out
    assert main(["pulse", "notify", "--apply", "--profile", str(project)]) == 0
    assert lark.calls == 1                                                       # второй раз за день — нет
    events = [r for r in publications.read(Instance(project, today=TODAY)) if r["event"] == "notify"]
    assert len(events) == 1 and events[0]["confirmed"] is True


def test_notify_not_connected_prints_text(project, at_today, capsys):
    assert main(["pulse", "run", "--no-network", "--profile", str(project)]) == 0
    assert main(["pulse", "notify", "--apply", "--profile", str(project)]) == 0
    assert "не подключено" in capsys.readouterr().out
