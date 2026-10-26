"""Записи в день выкладки (Т23, П34) и видимость новой статьи анти-дублям сразу (П35)."""
from datetime import date, timedelta

import pytest

from content_engine.cli import main
from content_engine.core import bets, publications, store
from content_engine.core.instance import Instance
from content_engine.core.urls import norm_url
from content_engine.steps import queue as queue_mod
from content_engine.steps import records as records_mod
from content_engine.steps import reservations
from content_engine.steps.demand_map import build_map
from content_engine.steps.gate_topic import Candidate, gate

from .conftest import TODAY, set_profile
from .site_fixture import Page
from .test_brief import FORMULATIONS, TID, inst  # noqa: F401
from .test_verify import URL, Site, built

DB_ADAPTER = '''"""Фейковая база проекта: строка в JSON-файле инстанса."""
import json
from pathlib import Path

from content_engine.core.actions import Action


class Records:
    def __init__(self, instance):
        self.path = Path(instance.root) / "db.json"

    def _rows(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else []

    def exists(self, tid, url):
        return any(r["id"] == tid for r in self._rows())

    def plan(self, tid, url):
        row = {"id": tid, "url": url, "status": "опубликована"}

        def do():
            self.path.write_text(json.dumps(self._rows() + [row], ensure_ascii=False), encoding="utf-8")
            return row

        def verify(_):
            back = next((r for r in self._rows() if r["id"] == tid), None)
            return back == row, "строка прочитана обратно" if back == row else "строки нет"

        return [Action("db_row", str(self.path), "запись строки", f"строка {tid}", "read_back", ("records", "write"),
                       do=do, verify=verify, snapshot=lambda: {"exists": self.exists(tid, url), "content": self._rows(),
                                                               "read_from": str(self.path)})]

    def rollback_note(self, tid, url):
        return f"удалить строку {tid} из db.json"
'''


@pytest.fixture
def released(inst, project):  # noqa: F811
    """Тема с бронью, пакетом и подтверждённым «доехало» на следующий день после сборки."""
    from .test_demand_map import cluster
    clusters = store.read_json(inst.file("clusters"), "clusters")["records"]
    store.write_json(inst.file("clusters"), "clusters", clusters + [cluster(inst, "C5", FORMULATIONS[0], FORMULATIONS)])
    build_map(inst, offline=True)
    r = reservations.create(inst, topic=FORMULATIONS[0], key=inst.norm.key(FORMULATIONS[0]).to_list(),
                            cluster_id="C5", who="тест", queue_id=TID)
    topics = queue_mod.load(inst)
    topics[0].update(reservation_id=r["id"], cluster_id="C5", bet={"hypothesis": "статья даст заявки",
                                                                    "expectation": "2", "fail_threshold": "0"})
    queue_mod.save(inst, topics)
    journal = project.parent / "журнал" / "{yyyy-mm}.md"
    set_profile(project, lambda d: d["publish"].update(journal_path=str(journal)))
    site = Site()
    site.pages[norm_url(URL)] = Page(200, built(Instance(project, today=TODAY)))
    day = TODAY + timedelta(days=1)
    import content_engine.cli as cli
    real = cli.open_instance
    cli.open_instance = lambda args, **kw: real(args, today=day, transport=site)
    try:
        assert main(["publish", "verify", TID, "--url", URL, "--no-browser", "--profile", str(project)]) == 0
    finally:
        cli.open_instance = real
    return Instance(project, today=day, transport=site), day


def test_all_records_after_verify(released):
    instance, day = released
    topic = queue_mod.load(instance)[0]
    assert topic["status"] == queue_mod.PUBLISHED and topic["published"] == day.isoformat()
    reservation = reservations.load(instance)[0]
    assert reservation["status"] == reservations.CONVERTED and reservation["url"] == norm_url(URL)
    data = store.read_json(instance.file("demand_map"), "demand_map")
    page = next(p for p in data["records"] if p["id"] == "posadka-chesnoka-pod-zimu")
    assert page["source"] == "запись выпуска" and page["cluster_id"] == "C5" and page["type"] == "article"
    assert page["target_keys"] == FORMULATIONS
    assert next(c for c in data["clusters"] if c["id"] == "C5")["status"] == "covered"
    bet = bets.load(instance)[0]
    assert bet["published"] == day.isoformat()
    assert bet["measure"] == {"90": (day + timedelta(days=90)).isoformat(),
                              "180": (day + timedelta(days=180)).isoformat()}
    journal = instance.root / "журнал" / f"{day.isoformat()[:7]}.md"
    assert f"content-engine: {TID}" in journal.read_text(encoding="utf-8")


def test_second_record_adds_nothing(released):
    instance, _ = released
    before = {name: instance.file(name).read_text(encoding="utf-8") for name in ("queue", "reservations", "bets",
                                                                                  "demand_map")}
    out = records_mod.record(instance, TID)
    assert out["new"] == 0
    assert all(instance.file(name).read_text(encoding="utf-8") == text for name, text in before.items())


def test_expired_reservation_converted_with_note(inst, project):  # noqa: F811
    r = reservations.create(inst, topic=FORMULATIONS[0], key=[], cluster_id=None, who="тест", queue_id=TID)
    topics = queue_mod.load(inst)
    topics[0]["reservation_id"] = r["id"]
    queue_mod.save(inst, topics)
    late = Instance(project, today=TODAY + timedelta(days=40))
    from content_engine.steps.reservations import expire
    expire(late)
    assert reservations.load(late)[0]["status"] == reservations.EXPIRED
    assert queue_mod.load(late)[0]["status"] == queue_mod.TAKEN           # взятая тема бронь не теряет
    publications.log(late, "published", id=TID, url=norm_url(URL), day=late.today.isoformat(), built="2026-03-02",
                     slug="posadka-chesnoka-pod-zimu", title="Посадка чеснока", key=[], cluster_id=None,
                     target_keys=[], topic=FORMULATIONS[0])
    out = records_mod.record(late, TID)
    assert reservations.load(late)[0]["status"] == reservations.CONVERTED
    assert any("бронь истекла" in line for line in out["lines"])


def test_project_db_snapshot_and_read_back(inst, project):  # noqa: F811
    (project.parent / "adapters").mkdir()
    (project.parent / "adapters" / "db.py").write_text(DB_ADAPTER, encoding="utf-8")
    set_profile(project, lambda d: d["records"].update(adapter="module:adapters/db.py:Records", allow="files"))
    publications.log(inst, "published", id=TID, url=norm_url(URL), day=TODAY.isoformat(), built=TODAY.isoformat(),
                     slug="posadka-chesnoka-pod-zimu", title="Посадка чеснока", key=[], cluster_id=None,
                     target_keys=[], topic=FORMULATIONS[0])
    out = records_mod.record(Instance(project, today=TODAY), TID, apply=True)
    assert any("records.allow = write" in line for line in out["lines"])
    assert not (project.parent / "db.json").exists()
    set_profile(project, lambda d: d["records"].update(allow="write"))
    out = records_mod.record(Instance(project, today=TODAY), TID, apply=True)
    line = next(x for x in out["lines"] if x.startswith("база проекта"))
    assert "записано 1 — подтверждено 1" in line and "удалить строку" in " ".join(out["lines"])
    snaps = list((project.parent / "data" / "snapshots" / "before" / TID).rglob("*.json"))
    assert len(snaps) == 1
    out = records_mod.record(Instance(project, today=TODAY), TID, apply=True)
    assert "строка уже есть" in " ".join(out["lines"])


def test_new_article_overlaps_at_once_and_after_map_build(released):
    instance, day = released
    cand = Candidate(FORMULATIONS[0], list(FORMULATIONS))
    report = gate(instance, cand, offline=True)
    assert report["verdict"] == "пересекается"
    assert report["stopped_by"]["layer"] == 1 and "записи выпуска" in report["stopped_by"]["reason"]
    build_map(instance, offline=True)
    data = store.read_json(instance.file("demand_map"), "demand_map")
    assert any(p["id"] == "posadka-chesnoka-pod-zimu" for p in data["records"])
    assert next(c for c in data["clusters"] if c["id"] == "C5")["status"] == "covered"
    again = gate(Instance(instance.profile_path, today=day), Candidate(FORMULATIONS[0], list(FORMULATIONS)),
                 offline=True)
    assert again["verdict"] == "пересекается" and again["stopped_by"]["layer"] == 1


def test_record_before_verify_refused(inst):  # noqa: F811
    from content_engine.core.errors import GateRefused
    with pytest.raises(GateRefused, match="сначала `publish verify"):
        records_mod.record(inst, TID)
    assert date.fromisoformat(TODAY.isoformat())
