"""Профиль версии 4 и перевод `3 → 4` (Т15.1, Т43.1): пульс, книга ставок, аудит, расписание — поля без «молчаливых»
значений; задание расписания — только существующая команда с `--unattended`."""
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from content_engine.cli import main
from content_engine.core import migrate
from content_engine.core import profile as profile_mod
from content_engine.core.errors import ProfileError


def calibrated_v3(tmp_path: Path) -> Path:
    """Профиль версии 3 с калибровкой проекта: свой уровень, комментарий и два направления."""
    text = profile_mod.template_text(3)
    text = text.replace("  body_warn: 25 ", "  body_warn: 30 ")
    text = text.replace("  lock_minutes: 60 ", "  # своя блокировка проекта\n  lock_minutes: 90 ")
    text = text.replace("    volume: [6000, 12000]             # коридор объёма статьи направления, знаков без разметки\n",
                        "    volume: [6000, 12000]             # коридор объёма статьи направления, знаков без разметки\n"
                        "  - name: второе\n    words: [второе]\n    weight: 2\n    quarter_quota: 3\n"
                        "    volume: [3000, 8000]\n")
    path = tmp_path / "profile.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_template_is_version_4():
    data = yaml.safe_load(profile_mod.template_text(4))
    assert data["schema_version"] == 4
    profile_mod.validate(data, 4)
    assert data["notify"]["allow"] == "files" and data["schedule"]["allow"] == "files"
    assert data["audit"]["false_alarm"]["rate"] == -1


def test_v3_profile_refused_with_migrate_hint(tmp_path, capsys):
    path = calibrated_v3(tmp_path)
    assert main(["queue", "status", "--profile", str(path)]) == 3
    err = capsys.readouterr().err
    assert "профиль версии 3" in err and "profile migrate" in err


def test_migrate_3_to_4_plan_changes_nothing(tmp_path, capsys):
    path = calibrated_v3(tmp_path)
    before = digest(path)
    assert main(["profile", "migrate", "--profile", str(path)]) == 0
    out = capsys.readouterr().out
    assert digest(path) == before
    assert "раздел monitor" in out and "раздел schedule" in out and "publish.verify_retries" in out
    assert "directions[0].hypothesis_id" in out and "directions[1].hypothesis_id" in out


def test_migrate_3_to_4_keeps_calibration_and_comments(tmp_path, capsys):
    path = calibrated_v3(tmp_path)
    old = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert main(["profile", "migrate", "--profile", str(path), "--apply"]) == 0
    assert re.search(r"записано строк \d+ — прочитано \d+", capsys.readouterr().out)
    text = path.read_text(encoding="utf-8")
    new = yaml.safe_load(text)
    profile_mod.validate(new, 4)
    assert new["schema_version"] == 4
    assert new["thresholds"]["body_warn"] == 30 and new["publish"]["lock_minutes"] == 90
    assert "# своя блокировка проекта" in text
    assert new["publish"]["verify_retries"] == 2
    assert [d["hypothesis_id"] for d in new["directions"]] == ["", ""]
    assert [d["weight"] for d in new["directions"]] == [d["weight"] for d in old["directions"]]
    assert len(re.findall(r"^publish:", text, re.M)) == 1
    for key, value in old.items():
        if key not in ("schema_version", "publish", "directions"):
            assert new[key] == value, key


def test_v1_reaches_v5_in_four_runs(tmp_path):
    from .conftest import FIXTURES
    path = tmp_path / "profile.yaml"
    path.write_text((FIXTURES / "profile_v1.yaml").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    for frm, to in ((1, 2), (2, 3), (3, 4), (4, 5)):
        step = migrate.plan(path)
        assert (step.version_from, step.version_to) == (frm, to)
        migrate.apply(path, step)
        profile_mod.validate(yaml.safe_load(path.read_text(encoding="utf-8")), to)
    profile_mod.load(path)


def job(**kw):
    return {"name": "pulse", "command": "spend report", "every": "day", "at": "09:00", "weekday": "", **kw}


@pytest.mark.parametrize("change, field", [
    (lambda d: d["notify"].update(allow="publish"), "notify.allow"),
    (lambda d: d["schedule"].update(allow="publish"), "schedule.allow"),
    (lambda d: d["notify"].update(kind="lark_webhook"), "notify.url_env"),
    (lambda d: d["notify"].update(kind="telegram", url_env="TG_TOKEN"), "notify.chat_env"),
    (lambda d: d["notify"].update(kind="pigeon"), "notify.kind"),
    (lambda d: d["bets"]["money"].update(kind="file"), "bets.money.file"),
    (lambda d: d["bets"].update(min_step=5), "bets.min_step"),
    (lambda d: d["audit"]["false_alarm"].update(rate=0.3), "audit.false_alarm.as_of"),
    (lambda d: d["audit"]["false_alarm"].update(rate=1.5, as_of="2026-10-26", source="П10"), "audit.false_alarm.rate"),
    (lambda d: d["monitor"].update(drift_shrink=1.2), "monitor.drift_shrink"),
    (lambda d: d["monitor"].update(tops=[]), "monitor.tops"),
    (lambda d: d["week_plan"].update(min=9), "week_plan"),
    (lambda d: d["schedule"].update(jobs=[job(command="pulse fly")]), "schedule.jobs[0].command"),
    (lambda d: d["schedule"].update(jobs=[job(command="spend report")]), "--unattended"),
    (lambda d: d["schedule"].update(jobs=[job(command="cluster", every="week")]), "schedule.jobs[0].weekday"),
    (lambda d: d["schedule"].update(jobs=[job(command="cluster", weekday="mon")]), "schedule.jobs[0].weekday"),
    (lambda d: d["schedule"].update(jobs=[job(command="cluster", at="9:00")]), "schedule.jobs[0].at"),
    (lambda d: d["schedule"].update(jobs=[job(command="cluster"), job(command="map build")]), "schedule.jobs[1].name"),
    (lambda d: d["directions"][0].pop("hypothesis_id"), "directions[0].hypothesis_id"),
])
def test_semantic_checks_v4(change, field):
    data = yaml.safe_load(profile_mod.template_text())
    change(data)
    with pytest.raises(ProfileError, match=re.escape(field)):
        profile_mod.validate(data)


def test_scheduled_job_with_unattended_is_valid():
    data = yaml.safe_load(profile_mod.template_text())
    data["schedule"]["jobs"] = [job(name="cluster", command="cluster", every="week", weekday="mon")]
    profile_mod.validate(data)


def test_corpus_role_date_allowed(project):
    from content_engine.adapters.corpus import ROLES
    assert "date" in ROLES
