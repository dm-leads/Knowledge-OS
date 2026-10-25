"""Находки независимого ревью (агент с чистым контекстом и вторая модель, 25.10.2026) — каждая закрыта тестом."""
import json

import pytest

from content_engine.adapters.fake import FakeSerp
from content_engine.adapters.serp import OK, SerpResult
from content_engine.cli import main
from content_engine.core import store
from content_engine.core.errors import EngineError, GateRefused, SourceError, StoreError
from content_engine.core.instance import Instance
from content_engine.core.quota import fetch_serp
from content_engine.core.serp_cache import SerpCache
from content_engine.steps import queue as queue_mod
from content_engine.steps import reservations
from content_engine.steps.demand_map import build_map
from content_engine.steps.gate_topic import Candidate, gate

from .conftest import TODAY
from .keysso_transport import KeyssoTransport, Response
from .test_demand_map import setup
from .test_layers import SERP
from .test_queue import BET, topic

GARLIC = ["посадка чеснока под зиму", "когда сажать чеснок", "как сажать чеснок осенью"]


@pytest.fixture
def instance(project, monkeypatch):
    setup(project)
    serp = FakeSerp(SERP)
    build_map(Instance(project, today=TODAY, serp=serp))
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, today=TODAY, serp=serp))
    return Instance(project, today=TODAY, serp=serp)


def gated_garlic(instance, project):
    queue_mod.save(instance, [topic("T0001", topic=GARLIC[0], formulations=GARLIC)])
    assert main(["gate", "topic", "--profile", str(project), "--from-queue", "T0001", "--reserve"]) == 0
    return Instance(project, today=TODAY, serp=instance.serp())


def test_exclude_makes_gate_incomplete(instance):
    report = gate(instance, Candidate(GARLIC[0], GARLIC, bet=BET, skip_slugs=frozenset({"gazon-vesnoj"})))
    assert not report["complete"] and report["flags"]["exclude"] == ["gazon-vesnoj"]


def test_no_demand_map_makes_gate_incomplete(project):
    inst = Instance(project, today=TODAY, serp=FakeSerp(SERP))
    report = gate(inst, Candidate(GARLIC[0], GARLIC, bet=BET))
    assert not report["complete"]


def test_released_reservation_returns_topic_and_blocks_take(instance, project):
    inst = gated_garlic(instance, project)
    rid = queue_mod.load(inst)[0]["reservation_id"]
    assert main(["reserve", "release", "--profile", str(project), rid]) == 0
    assert queue_mod.load(inst)[0]["status"] == queue_mod.CANDIDATE
    with pytest.raises(GateRefused):
        queue_mod.take(inst, "T0001")


def test_take_checks_report_itself(instance, project):
    inst = gated_garlic(instance, project)
    topics = queue_mod.load(inst)
    path = topics[0]["gate"]["report"]
    data = json.loads(open(path, encoding="utf-8").read())
    data["topic"] = "другая тема"
    open(path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
    with pytest.raises(GateRefused):
        queue_mod.take(inst, "T0001")


def test_take_refuses_commercial_serp_today(instance, project):
    inst = gated_garlic(instance, project)
    shops = [["market.example/a", 1], ["shop.example/b", 2], ["market.example/c", 3]]
    serp = FakeSerp({"remaining": 100, "top": {"msk": {f: shops for f in GARLIC}}})
    today = Instance(project, today=TODAY, serp=serp)
    today.profile["thresholds"]["commercial_min"] = 2
    for f in GARLIC:                                  # вчерашний кэш не годится — снимаем сегодня заново
        p = SerpCache(today.root, 45, TODAY)._path(f, "msk")
        p.unlink(missing_ok=True)
    out = queue_mod.take(today, "T0001")
    assert not out["taken"] and queue_mod.load(today)[0]["status"] == queue_mod.REJECTED


def test_regate_with_overlap_releases_reservation(instance, project):
    inst = gated_garlic(instance, project)
    report = {"date": TODAY.isoformat(), "verdict": "пересекается", "complete": True, "queue_id": "T0001",
              "stopped_by": {"layer": 3, "reason": "общая выдача", "page": "x", "section": ""},
              "serp_type": {"reason": ""}}
    queue_mod.record_gate(inst, "T0001", report, "отчёт.json")
    assert queue_mod.load(inst)[0]["status"] == queue_mod.REJECTED
    assert not reservations.active(inst)


def test_wrong_layers_is_input_error(instance, project):
    assert main(["gate", "topic", "--profile", str(project), GARLIC[0], "-f", GARLIC[0], "-f", GARLIC[1],
                 "-f", GARLIC[2], "--layers", "99"]) == 3


def test_negative_max_quota_refused(instance):
    with pytest.raises(EngineError):
        fetch_serp(instance, ["новая фраза"], max_quota=-1)


def test_refill_budget_is_for_whole_run(instance):
    queue_mod.save(instance, [topic(f"T{i:04d}", topic=f"тема {i}", formulations=[f"ф{i} а", f"ф{i} б", f"ф{i} в"])
                              for i in range(1, 4)])
    serp = FakeSerp({"remaining": 100, "top": {"msk": {f"ф{i} {x}": [["z.example/1", 1]]
                                                       for i in range(1, 4) for x in "абв"}}})
    inst = Instance(instance.profile_path, today=TODAY, serp=serp)
    queue_mod.refill(inst, count=3, max_quota=4)
    assert len(serp.requested) <= 4


def test_broken_cache_entry_is_missing(tmp_path):
    cache = SerpCache(tmp_path, 45, TODAY)
    cache.put(SerpResult("фраза", "msk", OK, [("a.example/1", 1)]))
    cache._path("фраза", "msk").write_text("{битый", encoding="utf-8")
    assert cache.get("фраза", "msk") is None


def test_non_object_registry_is_store_error(tmp_path):
    (tmp_path / "q.json").write_text("[]", encoding="utf-8")
    with pytest.raises(StoreError):
        store.read_json(tmp_path / "q.json", "queue")


def test_meta_lists_reconciled(tmp_path, monkeypatch):
    real = store._dump

    def lossy(path, text):
        data = json.loads(text)
        data["clusters"] = data["clusters"][:-1]
        real(path, json.dumps(data))

    monkeypatch.setattr(store, "_dump", lossy)
    with pytest.raises(StoreError, match="clusters"):
        store.write_json(tmp_path / "m.json", "demand_map", [{"id": 1}], meta={"clusters": [1, 2]})


def test_html_instead_of_json_is_source_error():
    from content_engine.adapters.keysso import KeyssoSerp

    class Html(Response):
        def json(self):
            raise ValueError("не JSON")

    serp = KeyssoSerp("ключ", transport=KeyssoTransport(script={"limits/all": [Html(200, text="<html>")]}),
                      sleep=lambda _: None)
    with pytest.raises(SourceError, match="не JSON"):
        serp.remaining()


def test_unexpected_crash_is_exit_three(instance, project, monkeypatch):
    import content_engine.steps.check as check

    def boom(_):
        raise KeyError("неожиданно")

    monkeypatch.setattr(check, "run_check", boom)
    assert main(["check", "--profile", str(project)]) == 3
