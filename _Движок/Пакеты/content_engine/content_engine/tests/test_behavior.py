"""Поведение на странице (Д6, Т38, П57): обрыв больше 70 % на шаге со 100 людьми — находка, с 60 — нет; ровные
соседи; тупик, устройство, выпавшая из группы; решение — человеком; замер на 14-й и 28-й день."""
from datetime import timedelta

import pytest

from content_engine.core import publications, store
from content_engine.core.errors import GateRefused
from content_engine.core.instance import Instance
from content_engine.steps import behavior, refresh

from .conftest import TODAY, set_profile

B = {"min_people": 100, "drop_share": 0.7, "device_ratio": 1.5}


def test_drop_on_hundred_people_found_on_sixty_not():
    found = behavior.funnel_findings(B, [("a", 130), ("b", 120), ("c", 100), ("d", 20), ("e", 18)])
    assert [f["url"] for f in found] == ["c"]                          # 100 → 20: уходят 80 %, соседи ровные
    assert not behavior.funnel_findings(B, [("a", 70), ("b", 66), ("c", 60), ("d", 12), ("e", 11)])


def test_uneven_neighbours_not_a_drop():
    assert not behavior.funnel_findings(B, [("a", 1000), ("b", 900), ("c", 300), ("d", 20)])


def page(url, users, bounce, depth=2.0, goals=1):
    return {"url": url, "users": users, "visits": users, "bounce": bounce, "depth": depth, "goals": goals}


def test_dead_end_device_outlier():
    pages = [page("example.ru/blog/a", 150, 0.3, depth=1.05, goals=0), page("example.ru/blog/b", 80, 0.2, depth=1.0,
                                                                            goals=0)]
    pages += [page(f"example.ru/blog/p{n}", 200, 0.20 + 0.01 * n) for n in range(6)]
    pages += [page("example.ru/blog/vypala", 200, 0.9)]
    devices = [{"url": "example.ru/blog/p1", "device": "mobile", "users": 120, "bounce": 0.6},
               {"url": "example.ru/blog/p1", "device": "desktop", "users": 150, "bounce": 0.2},
               {"url": "example.ru/blog/p2", "device": "tablet", "users": 20, "bounce": 0.9}]
    kinds = {(f["kind"], f["url"]) for f in behavior.page_findings(B, pages, devices)}
    assert ("dead_end", "example.ru/blog/a") in kinds and ("dead_end", "example.ru/blog/b") not in kinds
    assert ("device", "example.ru/blog/p1") in kinds and not any(u == "example.ru/blog/p2" for k, u in kinds
                                                                    if k == "device")
    assert ("outlier", "example.ru/blog/vypala") in kinds


def test_behavior_not_connected(project):
    out = behavior.scan(Instance(project, today=TODAY))
    assert out["status"] == "не подключено" and "metrika" in out["why"]


class FakeMetrika:
    """Отчёты Метрики: окно до дня публикации и после — разные числа."""

    def __init__(self, before, after, pub_day):
        self.before, self.after, self.pub = before, after, pub_day.isoformat()

    def _pick(self, frm):
        return self.after if frm >= self.pub else self.before

    def entry_pages(self, frm, to):
        return [page("example.ru/blog/a", 150, self._pick(frm), depth=1.05, goals=0)]

    def by_device(self, frm, to):
        return []

    def step_users(self, frm, to, step):
        return 100.0


@pytest.fixture
def metrika_on(project, monkeypatch):
    monkeypatch.setenv("M_TOKEN", "токен")
    monkeypatch.setenv("M_COUNTER", "1")
    set_profile(project, lambda d: d["behavior"].update(kind="metrika", token_env="M_TOKEN", counter_env="M_COUNTER"))
    return project


def test_accept_only_with_enough_people(metrika_on, monkeypatch):
    i = Instance(metrika_on, today=TODAY)
    monkeypatch.setattr(behavior, "_client", lambda instance, transport=None: FakeMetrika(0.5, 0.5, TODAY))
    out = behavior.scan(i)
    assert [f["id"] for f in out["findings"]] == ["dead_end|example.ru/blog/a"]
    with pytest.raises(GateRefused):
        behavior.accept(i, "dead_end|net", "Человек")
    findings = store.read_json(next((metrika_on.parent / "data" / "behavior").glob("20*.json")), "behavior")
    findings["records"][0]["people"] = 60
    store.write_json(next((metrika_on.parent / "data" / "behavior").glob("20*.json")), "behavior",
                     findings["records"], meta={k: v for k, v in findings.items() if k not in ("records", "kind",
                                                                                              "schema_version")})
    with pytest.raises(GateRefused, match="min_people"):
        behavior.accept(i, "dead_end|example.ru/blog/a", "Человек")


def test_refresh_takes_only_accepted_behavior(metrika_on, monkeypatch):
    set_profile(metrika_on, lambda d: d["refresh"].update(allow_behavior=True))
    i = Instance(metrika_on, today=TODAY)
    monkeypatch.setattr(behavior, "_client", lambda instance, transport=None: FakeMetrika(0.5, 0.5, TODAY))
    behavior.scan(i)
    assert not any(r["kind"] == "behavior" for c in refresh.scan(i)["candidates"] for r in c["reasons"])
    behavior.accept(i, "dead_end|example.ru/blog/a", "Человек")
    got = [c["url"] for c in refresh.scan(i)["candidates"] if any(r["kind"] == "behavior" for r in c["reasons"])]
    assert got == ["example.ru/blog/a"]


@pytest.mark.parametrize("before, after, advice", [(1.05, 1.6, "перенести"), (1.05, 1.06, "откатить")])
def test_check_advises_spread_or_rollback(metrika_on, monkeypatch, before, after, advice):
    pub_day = TODAY - timedelta(days=30)
    accept_day = Instance(metrika_on, today=pub_day - timedelta(days=5))
    fake = FakeMetrika(before, after, pub_day)
    fake.entry_pages = lambda frm, to: [page("example.ru/blog/a", 150, 0.5,
                                             depth=after if frm >= pub_day.isoformat() else before, goals=0)]
    monkeypatch.setattr(behavior, "_client", lambda instance, transport=None: fake)
    behavior.scan(accept_day)
    behavior.accept(accept_day, "dead_end|example.ru/blog/a", "Человек")
    publications.log(Instance(metrika_on, today=pub_day), "published", id="refresh-a", url="example.ru/blog/a",
                     day=pub_day.isoformat())
    out = behavior.check(Instance(metrika_on, today=TODAY))
    done = [r for r in out["results"] if r["status"] == "ок"]
    assert [r["day"] for r in done] == [14, 28] and all(advice in r["advice"] for r in done)
    if advice == "откатить":
        assert "publish rollback refresh-a" in done[0]["advice"]
    early = behavior.check(Instance(metrika_on, today=pub_day + timedelta(days=10)))
    assert all(r["status"].startswith("рано") for r in early["results"])


def test_metrika_adapter_reads_reports_only():
    from content_engine.adapters.metrika import Metrika
    from content_engine.core.errors import EndpointNotAllowed
    from .keysso_transport import Response
    seen = []

    def api(method, url, params=None, headers=None, **kw):
        seen.append((method, url, params))
        assert headers["Authorization"] == "OAuth токен"
        return Response(200, {"data": [{"dimensions": [{"name": "https://example.ru/blog/a"}],
                                        "metrics": [150, 160, 35.0, 1.4, 0]}]})
    m = Metrika("токен", "42", transport=api)
    rows = m.entry_pages("2026-02-01", "2026-02-28")
    assert rows == [{"url": "https://example.ru/blog/a", "users": 150, "visits": 160, "bounce": 0.35, "depth": 1.4,
                     "goals": 0}]
    assert seen[0][1].endswith("/stat/v1/data") and seen[0][2]["ids"] == "42"
    with pytest.raises(EndpointNotAllowed):
        m.client.call("POST", "management/v1/counter/42/goals")
