"""Очередь, запас и запрет брать тему без гейта (Т11.4а, Т12, П7а)."""
from datetime import timedelta

import pytest

from content_engine.adapters.fake import FakeSerp
from content_engine.cli import main
from content_engine.core.errors import GateRefused
from content_engine.core.instance import Instance
from content_engine.steps import queue as queue_mod
from content_engine.steps import reservations
from content_engine.steps.demand_map import build_map

from .conftest import TODAY, set_profile
from .test_demand_map import setup
from .test_layers import SERP

BET = {"hypothesis": "статья даст заявки", "expectation": "2 заявки", "fail_threshold": "0 за 180 дней",
       "windows": [90, 180]}


def topic(tid, status=queue_mod.CANDIDATE, **extra):
    base = {"id": tid, "topic": f"тема {tid}", "formulations": [f"тема {tid}", f"вопрос {tid}", f"как {tid}"],
            "cluster_id": None, "direction": "без направления", "weight": 10, "manual_priority": 0,
            "status": status, "gate": None, "reservation_id": None, "bet": dict(BET), "created": "2026-03-01",
            "rejected": None, "prefilter": None}
    base.update(extra)
    return base


@pytest.fixture
def instance(project, monkeypatch):
    setup(project)
    serp = FakeSerp(SERP)
    build_map(Instance(project, today=TODAY, serp=serp))
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, today=TODAY, serp=serp))
    return Instance(project, today=TODAY, serp=serp)


def test_take_without_gate_refused_with_gate_taken(instance, project):
    queue_mod.save(instance, [topic("T0001", topic="обрезка малины осенью",
                                    formulations=["обрезка малины осенью", "как обрезать малину",
                                                  "когда обрезать малину"])])
    with pytest.raises(GateRefused, match="без отчёта полного гейта"):
        queue_mod.take(instance, "T0001")
    assert main(["gate", "topic", "--profile", str(project), "--from-queue", "T0001", "--reserve"]) == 2
    # «спорно»: наша страница в топ-10 без данных о деньгах — бронь не ставится, взять нельзя
    with pytest.raises(GateRefused):
        queue_mod.take(instance, "T0001")


def test_take_after_free_gate(instance, project):
    queue_mod.save(instance, [topic("T0001", topic="посадка чеснока под зиму",
                                    formulations=["посадка чеснока под зиму", "когда сажать чеснок",
                                                  "как сажать чеснок осенью"])])
    serp = instance.serp()
    code = main(["gate", "topic", "--profile", str(project), "--from-queue", "T0001", "--reserve"])
    assert code == 0
    fresh = Instance(project, today=TODAY, serp=serp)
    assert queue_mod.load(fresh)[0]["status"] == queue_mod.GATED
    assert queue_mod.take(fresh, "T0001")["taken"]
    assert queue_mod.load(fresh)[0]["status"] == queue_mod.TAKEN


def test_take_with_stale_gate_refused(instance, project):
    queue_mod.save(instance, [topic("T0001", topic="посадка чеснока под зиму",
                                    formulations=["посадка чеснока под зиму", "когда сажать чеснок",
                                                  "как сажать чеснок осенью"])])
    main(["gate", "topic", "--profile", str(project), "--from-queue", "T0001", "--reserve"])
    later = Instance(project, today=TODAY + timedelta(days=8), serp=instance.serp())
    with pytest.raises(GateRefused, match="за последние 7 дней"):
        queue_mod.take(later, "T0001")


def test_build_prefilters_and_refill_fills_stock(instance):
    out = queue_mod.build(instance)
    assert out["candidates"] == 1
    topics = queue_mod.load(instance)
    assert len(topics) == 1 and topics[0]["cluster_id"] == "C4"
    queue_mod.save(instance, topics + [topic("T0002", topic="посадка чеснока под зиму",
                                             formulations=["посадка чеснока под зиму", "когда сажать чеснок",
                                                           "как сажать чеснок осенью"], weight=99)])
    result = queue_mod.refill(instance, count=5)
    topics = queue_mod.load(instance)
    active = {r["id"] for r in reservations.active(instance)}
    gated = [t for t in topics if t["status"] == queue_mod.GATED and t["reservation_id"] in active]
    assert result["stock"] == len(gated) == queue_mod.stock(instance) >= 1


def test_status_stock_at_refill_threshold_exit_one(instance):
    queue_mod.save(instance, [topic(f"T{i:04d}") for i in range(1, 7)])
    topics = queue_mod.load(instance)
    for t in topics:
        r = reservations.create(instance, topic=t["topic"], key=[], cluster_id=None, who="тест", queue_id=t["id"])
        t.update(status=queue_mod.GATED, reservation_id=r["id"])
    queue_mod.save(instance, topics)
    report, code = queue_mod.status(instance)
    assert report["stock"] == 6 and code == 1 and report["below_norm"]
    assert report["days"] == round(6 / 0.57, 1)


def test_empty_queue_is_error(instance):
    from content_engine.core.errors import EngineError
    with pytest.raises(EngineError, match="сбор спроса не дал кандидатов"):
        queue_mod.status(instance)


def test_gate_exit_codes(instance, project):
    p = str(project)
    free = ["gate", "topic", "--profile", p, "посадка чеснока под зиму", "-f", "посадка чеснока под зиму",
            "-f", "когда сажать чеснок", "-f", "как сажать чеснок осенью"]
    set_profile(project, lambda d: d["queue"].update(require_bet=False))
    assert main(free) == 0
    assert main(["gate", "topic", "--profile", p, "отопление теплицы зимой", "-f", "отопление теплицы зимой",
                 "-f", "как отапливать теплицу", "-f", "чем топить теплицу"]) == 1
    assert main(["gate", "topic", "--profile", p, "обрезка малины осенью", "-f", "обрезка малины осенью", "-f",
                 "как обрезать малину", "-f", "когда обрезать малину"]) == 2
    assert main(["gate", "topic", "--profile", p, "тема", "-f", "тема"]) == 3


def test_require_bet_makes_disputed(instance):
    from content_engine.steps.gate_topic import Candidate, gate
    cand = Candidate("посадка чеснока под зиму", ["посадка чеснока под зиму", "когда сажать чеснок",
                                                 "как сажать чеснок осенью"])
    instance.profile["queue"]["require_bet"] = True          # в шаблоне версии 2 — false (Т15.2)
    assert gate(instance, cand)["verdict"] == "спорно"
    instance.profile["queue"]["require_bet"] = False
    assert gate(instance, cand)["verdict"] == "свободно"


def test_build_sees_topics_of_same_run(instance):
    from content_engine.core import store
    data = store.read_json(instance.file("demand_map"), "demand_map")
    first = dict(data["candidates"][0], queries=["обрезка малины", "как обрезать малину", "когда обрезать малину"])
    data["candidates"][0] = first
    twin = dict(first, cluster_id="C5")
    store.write_json(instance.file("demand_map"), "demand_map", data["records"],
                     meta={k: v for k, v in data.items() if k not in ("records", "schema_version", "kind")}
                     | {"candidates": data["candidates"] + [twin]})
    queue_mod.build(instance)
    topics = queue_mod.load(instance)
    assert [t["status"] for t in topics] == [queue_mod.CANDIDATE, queue_mod.REJECTED]
    assert topics[1]["rejected"]["layer"] == 1 and "T0001" in topics[1]["rejected"]["reason"]
