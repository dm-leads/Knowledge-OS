"""Расписание одной командой и план недели (Т42.1–Т42.3, П61)."""
import hashlib
import json
import re

import pytest

from content_engine.cli import main
from content_engine.core import audit_store, publications, store
from content_engine.core.instance import Instance
from content_engine.schedulers import base
from content_engine.schedulers.windows import WindowsScheduler, xml
from content_engine.steps import schedule, week_plan

from .conftest import TODAY, set_profile

JOBS = [{"name": "pulse", "command": "pulse run", "every": "day", "at": "09:00", "weekday": ""},
        {"name": "audit", "command": "audit run", "every": "week", "at": "09:30", "weekday": "mon"}]


class FakeSchtasks:
    """Подмена schtasks: хранит задания (имя → XML); одно чужое задание стоит заранее."""

    def __init__(self):
        self.tasks = {"Чужое-задание": "<Task><Command>other</Command></Task>"}
        self.changes = 0

    def __call__(self, args, input_text=None):
        if args[:2] == ["schtasks", "/Query"] and "/XML" in args:
            name = args[args.index("/TN") + 1]
            return (0, self.tasks[name]) if name in self.tasks else (1, "ERROR: not found")
        if args[:2] == ["schtasks", "/Query"]:
            return 0, "\n".join(f'"\\{n}","N/A","Ready"' for n in self.tasks)
        if args[:2] == ["schtasks", "/Create"]:
            self.changes += 1
            name, path = args[args.index("/TN") + 1], args[args.index("/XML") + 1]
            self.tasks[name] = open(path, encoding="utf-16").read()
            return 0, "SUCCESS"
        if args[:2] == ["schtasks", "/Delete"]:
            self.changes += 1
            self.tasks.pop(args[args.index("/TN") + 1], None)
            return 0, "SUCCESS"
        return 1, "?"


@pytest.fixture
def planned(project):
    set_profile(project, lambda d: d["schedule"].update(jobs=JOBS))
    return project


def test_install_without_right_or_apply_is_plan_only(planned, capsys):
    fake = FakeSchtasks()
    instance = Instance(planned, today=TODAY)
    schedule.install(instance, apply=False, runner=fake)
    assert fake.changes == 0 and "план" in capsys.readouterr().out
    with pytest.raises(Exception, match="schedule.allow"):
        schedule.install(instance, apply=True, runner=fake)
    assert fake.changes == 0


def test_install_confirmed_by_read_back(planned):
    set_profile(planned, lambda d: d["schedule"].update(allow="write"))
    fake = FakeSchtasks()
    out = schedule.install(Instance(planned, today=TODAY), apply=True, runner=fake)
    assert out["line"] == "записано 2 — подтверждено 2"
    names = [n for n in fake.tasks if n.startswith(base.PREFIX)]
    assert len(names) == 2 and all(re.fullmatch(r"[A-Za-z0-9-]+", n) for n in names)
    assert "--unattended" in fake.tasks[names[0]]


def test_xml_has_start_when_available_and_cyrillic_path(tmp_path):
    job = base.Job("KOS-content-engine-ab12cd34-pulse",
                   'py -3 -m content_engine pulse run --profile "C:\\Проекты\\Блог — машина\\profile.yaml" --unattended',
                   "C:\\Проекты\\Скрипты", "week", "09:00", "mon")
    text = xml(job, on_boot=True)
    path = tmp_path / "t.xml"
    path.write_text(text, encoding="utf-16")
    back = path.read_text(encoding="utf-16")
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in back and "Блог — машина" in back
    assert "<Monday />" in back and "2026-01-01T09:00:00" in back
    assert "<StartWhenAvailable>false</StartWhenAvailable>" in xml(job, on_boot=False)


def test_remove_takes_only_own(planned):
    set_profile(planned, lambda d: d["schedule"].update(allow="write"))
    fake = FakeSchtasks()
    instance = Instance(planned, today=TODAY)
    schedule.install(instance, apply=True, runner=fake)
    schedule.remove(instance, job="pulse", apply=True, runner=fake)
    left = sorted(fake.tasks)
    assert "Чужое-задание" in left and any(n.endswith("-audit") for n in left)
    assert not any(n.endswith("-pulse") for n in left)
    schedule.remove(instance, job=None, apply=True, runner=fake)
    assert sorted(fake.tasks) == ["Чужое-задание"]


def test_list_reads_only(planned):
    set_profile(planned, lambda d: d["schedule"].update(allow="write"))
    fake = FakeSchtasks()
    instance = Instance(planned, today=TODAY)
    schedule.install(instance, apply=True, runner=fake)
    store.append_jsonl(planned.parent / "data" / "schedule_runs.jsonl",
                       [{"command": "pulse run", "start": "2026-03-02T09:00:05", "code": 0}])
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in planned.parent.rglob("*") if p.is_file()}
    lines = schedule.listing(Instance(planned, today=TODAY), runner=fake)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in planned.parent.rglob("*") if p.is_file()}
    assert before == after and fake.changes == 2
    assert any("pulse" in l and "2026-03-02T09:00:05" in l for l in lines)


def test_unattended_run_is_logged_and_spends_nothing(planned, monkeypatch):
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, **{"today": TODAY, **kw}))
    assert main(["pulse", "run", "--no-network", "--unattended", "--profile", str(planned)]) == 0
    runs = store.read_jsonl(planned.parent / "data" / "schedule_runs.jsonl")
    assert runs[-1]["command"] == "pulse run" and runs[-1]["code"] == 0
    assert not (planned.parent / "data" / "spend.jsonl").exists()


def test_week_plan_five_to_eight_with_basis(project):
    instance = Instance(project, today=TODAY)
    audit_store.write(instance, "full", 3, [
        {"id": "title_empty|a", "url": "a", "check": "title_empty", "level": "critical", "detail": "", "first_seen": ""},
        {"id": "orphan|b", "url": "b", "check": "orphan", "level": "medium", "detail": "", "first_seen": ""}])
    publications.log(instance, "unverified", id="T0001", url="example.ru/blog/x", reason="сеть")
    store.write_json(instance.file("bets"), "bets", [{"id": "T0002", "url": "example.ru/blog/y", "published":
        "2026-01-01", "measure": {"90": "2026-03-10", "180": "2026-06-30"}, "measures": {
            "90": {"proposed": "сыграла", "result": None}}}])
    folder = project.parent / "data" / "pulse"
    folder.mkdir(parents=True)
    (folder / f"{TODAY.isoformat()}.json").write_text(json.dumps({"verdict": {
        "color": "красный", "rule": 2, "why": "публикаций нет 5 дн.", "todo": "`queue take`"},
        "blocks": {"positions": {"alarms": [{"kind": "падение", "engine": "google", "query": "q", "url": "u",
                                             "was": 3, "now": 9}]}}}, ensure_ascii=False), encoding="utf-8")
    data = week_plan.plan(instance)
    assert 5 <= len(data["tasks"]) <= 8 and data["note"] == ""
    assert all(t["number"] and t["source"] for t in data["tasks"])
    assert data["tasks"][0]["group"] == "пульс"


def test_week_plan_fewer_marked(project, monkeypatch):
    from content_engine.steps import check
    monkeypatch.setattr(check, "run_check", lambda inst: [])
    instance = Instance(project, today=TODAY)
    audit_store.write(instance, "full", 1, [])
    data = week_plan.plan(instance)
    assert len(data["tasks"]) < 5 and "меньше" in data["note"]


def test_week_plan_ignores_low_data_proposals(project, monkeypatch):
    from content_engine.steps import check
    monkeypatch.setattr(check, "run_check", lambda inst: [])
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [{"id": f"C:{i}", "url": f"example.ru/blog/{i}", "measures": {
        "90": {"proposed": "мало данных", "result": None}}} for i in range(5)])
    tasks = week_plan.plan(instance)["tasks"]
    assert not any("bet accept" in t["what"] for t in tasks)
