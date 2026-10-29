"""Расписание «раз в месяц» (З80, Ч3): день месяца — `schedule.month_day`; планировщик Windows — `ScheduleByMonth`,
cron — день месяца в строке; установка подтверждается чтением обратно, другой день — не «то же задание»."""
from content_engine.core.instance import Instance
from content_engine.schedulers import base
from content_engine.schedulers.cron import line
from content_engine.schedulers.windows import WindowsScheduler, xml
from content_engine.steps import schedule

from .conftest import TODAY, set_profile
from .test_schedule_week import FakeSchtasks

MONTH = [{"name": "field", "command": "field probe", "every": "month", "at": "08:00", "weekday": ""}]


def job(day=5):
    return base.Job("KOS-content-engine-ab12cd34-field", "py -3 -m content_engine field probe --unattended",
                    "C:\\Скрипты", "month", "08:00", "", day)


def test_month_xml_and_cron_line():
    text = xml(job(5), on_boot=True)
    assert "<ScheduleByMonth><DaysOfMonth><Day>5</Day></DaysOfMonth>" in text and "<December />" in text
    assert line(job(5)).startswith("0 8 5 * * ")
    day = base.Job("x", "c", "w", "day", "08:00", "")
    assert line(day).startswith("0 8 * * * ") and "<ScheduleByDay>" in xml(day, on_boot=True)


def test_month_job_installed_and_confirmed(project):
    set_profile(project, lambda d: (d["schedule"].update(jobs=MONTH, allow="write", month_day=7)))
    fake = FakeSchtasks()
    out = schedule.install(Instance(project, today=TODAY), apply=True, runner=fake)
    assert out["line"] == "записано 1 — подтверждено 1"
    name = next(n for n in fake.tasks if n.startswith(base.PREFIX))
    assert "<Day>7</Day>" in fake.tasks[name]
    sch = WindowsScheduler(runner=fake)
    got = sch.read(name)
    assert got["every"] == "month" and got["month_day"] == 7
    jobs = base.jobs(Instance(project, today=TODAY))
    assert sch.matches(jobs[0], got)
    other = base.Job(jobs[0].name, jobs[0].command, jobs[0].workdir, "month", jobs[0].at, "", 8)
    assert not sch.matches(other, got)


def test_template_jobs_unattended_spend_nothing(project, monkeypatch):
    import yaml
    import content_engine.cli as cli
    from content_engine.cli import main
    from content_engine.core import profile as profile_mod
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, **{"today": TODAY, **kw}))
    jobs = yaml.safe_load(profile_mod.template_text())["schedule"]["jobs"]
    assert {j["command"] for j in jobs} >= {"refresh scan", "ai score", "competitors snap", "freeze scan", "field probe"}
    for j in jobs:
        assert "--fetch" not in j["command"] and "--confirm-spend" not in j["command"]
        code = main(j["command"].split() + ["--profile", str(project), "--unattended"])
        assert code in (0, 1, 3), j["command"]
    assert not (project.parent / "data" / "spend.jsonl").exists()
