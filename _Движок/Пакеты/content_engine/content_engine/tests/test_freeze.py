"""Заморозка по динамике (А3, Т41.1, П60): кандидат по правилу без автоматической заморозки, сезонная яма — не
кандидат, ряд с пропуском — «нет данных», дозапрос облака — только с «да», без человека — ноль трат."""
import pytest

from content_engine.core import store
from content_engine.core.errors import BudgetRefused, GateRefused
from content_engine.core.instance import Instance
from content_engine.steps import freeze

from .conftest import TODAY

MONTHS = freeze.months_back(TODAY)
F = {"drop_share": 0.5, "min_months_down": 8, "min_frequency": 10}


def series(values):
    return {"months": dict(zip(MONTHS, values)), "as_of": TODAY.isoformat()}


def _clusters(i, heads):
    store.write_json(i.file("clusters"), "clusters",
                     [{"id": cid, "head": h, "queries": [{"query": h}], "weight": 10 - n}
                      for n, (cid, h) in enumerate(heads.items())])


def _history(i, phrase, values):
    freeze.merge(i, [{"phrase": phrase, "month": m, "count": v, "source": "test", "as_of": "2026-03-01"}
                     for m, v in zip(MONTHS, values)])


def test_months_back_are_full_months():
    assert MONTHS[-1] == "2026-02" and MONTHS[0] == "2024-03" and len(MONTHS) == 24


def test_steady_drop_is_candidate_not_frozen(project):
    got = freeze.judge(F, MONTHS, series([100] * 12 + [40] * 12))
    assert got["status"] == freeze.CANDIDATE and got["months_down"] == 12
    i = Instance(project, today=TODAY)
    _clusters(i, {"c1": "обогрев теплицы"})
    _history(i, "обогрев теплицы", [100] * 12 + [40] * 12)
    out = freeze.scan(i)
    assert out["counts"][freeze.CANDIDATE] == 1
    assert store.read_json(i.file("frozen"), "frozen", missing_ok=True)["records"] == []      # сам не замораживает


def test_seasonal_dip_not_candidate():
    year = [100, 100, 100, 100, 100, 100, 5, 5, 5, 5, 5, 100]
    assert freeze.judge(F, MONTHS, series(year + year))["status"] == freeze.OK
    shifted = [100] * 12 + [100, 100, 100, 100, 100, 100, 5, 5, 5, 5, 5, 5]
    assert freeze.judge(F, MONTHS, series(shifted))["status"] == freeze.OK       # −47 % и 6 мес. ниже — не устойчиво


def test_gap_in_series_is_no_data():
    s = series([100] * 12 + [40] * 12)
    del s["months"][MONTHS[5]]
    got = freeze.judge(F, MONTHS, s)
    assert got["status"] == freeze.NO_SERIES and "неполный" in got["why"]
    assert freeze.judge(F, MONTHS, None)["status"] == freeze.NO_SERIES
    assert freeze.judge(F, MONTHS, series([5] * 24))["status"] == freeze.LOW


def test_history_absorbed_from_demand_without_duplicates(project):
    i = Instance(project, today=TODAY)
    _clusters(i, {"c1": "обогрев теплицы"})
    row = {"query": "Обогрев теплицы", "source": "wordstat:cloud", "numbers": {"frequency": {"as_of": "2026-03-01"}},
           "history": [{"month": m, "count": 50} for m in MONTHS]}
    store.append_jsonl(i.file("demand"), [row])
    assert freeze.scan(i)["absorbed"] == 24
    assert freeze.scan(i)["absorbed"] == 0
    assert len(store.read_jsonl(freeze.history_path(i))) == 24


def test_fetch_needs_yes_and_unattended_spends_nothing(project, monkeypatch):
    from content_engine.adapters import wordstat
    calls = []

    class Cloud:
        def __init__(self, instance):
            pass

        def dynamics(self, phrase, months=24):
            calls.append(phrase)
            return [(m, 30.0) for m in MONTHS]
    monkeypatch.setattr(wordstat, "CloudWordstat", Cloud)
    i = Instance(project, today=TODAY)
    _clusters(i, {"c1": "обогрев теплицы", "c2": "полив газона"})
    with pytest.raises(BudgetRefused):
        freeze.scan(i, fetch=True)
    i.unattended = True
    with pytest.raises(BudgetRefused):
        freeze.scan(i, fetch=True)
    assert calls == []
    i = Instance(project, today=TODAY)
    i.confirm_spend = 1.0
    out = freeze.scan(i, fetch=True)
    assert sorted(calls) == ["обогрев теплицы", "полив газона"] and out["fetched"] == 48
    calls.clear()
    i = Instance(project, today=TODAY)
    i.confirm_spend = 1.0
    freeze.scan(i, fetch=True)
    assert calls == []                                    # ряд свежий — повторно не покупается


def test_freeze_only_by_accept_and_only_candidate(project):
    i = Instance(project, today=TODAY)
    _clusters(i, {"c1": "обогрев теплицы", "c2": "полив газона"})
    _history(i, "обогрев теплицы", [100] * 12 + [40] * 12)
    _history(i, "полив газона", [50] * 24)
    freeze.scan(i)
    with pytest.raises(GateRefused, match="только кандидата"):
        freeze.accept(i, "c2", "Человек")
    freeze.accept(i, "c1", "Человек")
    frozen = store.read_json(i.file("frozen"), "frozen")["records"]
    assert [r["cluster_id"] for r in frozen] == ["c1"] and "динамика спроса" in frozen[0]["reason"]
    freeze.accept(i, "c1", "Человек")
    assert len(store.read_json(i.file("frozen"), "frozen")["records"]) == 1


def test_only_top_clusters_by_weight(project):
    from .conftest import set_profile
    set_profile(project, lambda d: d["freeze"].update(top=1))
    i = Instance(project, today=TODAY)
    _clusters(i, {"c1": "обогрев теплицы", "c2": "полив газона"})
    out = freeze.scan(i)
    assert [r["cluster_id"] for r in out["rows"]] == ["c1"]


def test_zero_month_without_count_and_rows_kept_on_failure(project, monkeypatch):
    from content_engine.adapters import wordstat

    class Client:
        def call(self, method, endpoint, json=None):
            if json["phrase"] == "полив газона":
                raise wordstat.SourceError("облако не ответило")
            return {"results": [{"date": f"{m}-01T00:00:00Z", **({"count": "30"} if n % 2 else {})}
                                for n, m in enumerate(MONTHS)]}
    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "x")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "x")
    real = wordstat.CloudWordstat.__init__

    def init(self, instance):
        real(self, instance)
        self.client = Client()
    monkeypatch.setattr(wordstat.CloudWordstat, "__init__", init)
    i = Instance(project, today=TODAY)
    i.confirm_spend = 1.0
    _clusters(i, {"c1": "обогрев теплицы", "c2": "полив газона"})
    with pytest.raises(Exception):
        freeze.scan(i, fetch=True)
    rows = store.read_jsonl(freeze.history_path(i))
    assert len(rows) == 24 and {r["count"] for r in rows} == {0.0, 30.0}      # ряд до сбоя сохранён, ноль — ноль


def test_broken_run_spend_counts_received_answers(project, monkeypatch):
    from content_engine.adapters import wordstat
    calls = []

    class Cloud:
        def __init__(self, instance):
            pass

        def dynamics(self, phrase, months=24):
            if calls:
                raise wordstat.SourceError("429 — лимит исчерпан")
            calls.append(phrase)
            return [(m, 30.0) for m in MONTHS]
    monkeypatch.setattr(wordstat, "CloudWordstat", Cloud)
    i = Instance(project, today=TODAY)
    i.confirm_spend = 1.0
    _clusters(i, {"c1": "обогрев теплицы", "c2": "полив газона", "c3": "укрытие роз"})
    with pytest.raises(Exception):
        freeze.scan(i, fetch=True)
    rows = [r for r in store.read_jsonl(i.file("spend")) if r["step"] == "freeze"]
    price = i.p["sources"]["wordstat"]["cloud_price_rub"]
    assert rows[-1]["value"] == round(price * 1, 2) and "оплачено ответов" in rows[-1]["source"]


def test_missing_registry_names_next_step(project):
    from content_engine.core.errors import StoreError
    with pytest.raises(StoreError, match="сначала `content-engine demand collect`"):
        store.read_jsonl(Instance(project, today=TODAY).file("demand"))
