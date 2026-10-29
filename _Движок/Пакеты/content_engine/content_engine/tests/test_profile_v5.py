"""Профиль версии 5 и перевод `4 → 5` (Т15.1, Т43.2): дожим, ИИ-видимость, поведение, заморозка, разведка, «поле
шире»; расписание раз в месяц — днём месяца в разделе."""
import re
from pathlib import Path

import pytest
import yaml

from content_engine.cli import main
from content_engine.core import migrate
from content_engine.core import profile as profile_mod
from content_engine.core.errors import ProfileError


def calibrated_v4(tmp_path: Path) -> Path:
    text = profile_mod.template_text(4)
    text = text.replace("  drop: 5 ", "  # свой порог проекта\n  drop: 7 ")
    path = tmp_path / "profile.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_template_is_version_5():
    data = yaml.safe_load(profile_mod.template_text())
    assert data["schema_version"] == 5
    profile_mod.validate(data)
    assert data["behavior"]["kind"] == "none" and data["ai_visibility"]["citations"] == "none"
    assert data["ai_visibility"]["max_quota"] == 0
    assert data["gate_article"]["levels"]["refresh_change"] == "block"


def test_v4_profile_refused_with_migrate_hint(tmp_path, capsys):
    path = calibrated_v4(tmp_path)
    assert main(["queue", "status", "--profile", str(path)]) == 3
    assert "профиль версии 4" in capsys.readouterr().err


def test_migrate_4_to_5_keeps_calibration(tmp_path, capsys):
    path = calibrated_v4(tmp_path)
    old = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert main(["profile", "migrate", "--profile", str(path)]) == 0
    out = capsys.readouterr().out
    for line in ("раздел refresh", "раздел behavior", "раздел field", "schedule.month_day",
                 "gate_article.levels.refresh_change"):
        assert line in out
    assert main(["profile", "migrate", "--profile", str(path), "--apply"]) == 0
    assert main(["profile", "check", "--profile", str(path)]) == 0
    text = path.read_text(encoding="utf-8")
    new = yaml.safe_load(text)
    assert new["schema_version"] == 5 and new["monitor"]["drop"] == 7 and "# свой порог проекта" in text
    assert new["schedule"]["month_day"] == 1 and new["schedule"]["jobs"] == old["schedule"]["jobs"]
    assert len(re.findall(r"^schedule:", text, re.M)) == 1
    for key, value in old.items():
        if key not in ("schema_version", "schedule", "gate_article"):
            assert new[key] == value, key


def job(**kw):
    return {"name": "pulse", "command": "pulse run", "every": "day", "at": "09:00", "weekday": "", **kw}


def test_month_job_only_in_v5():
    data = yaml.safe_load(profile_mod.template_text())
    data["schedule"]["jobs"] = [job(every="month")]
    profile_mod.validate(data)
    old = yaml.safe_load(profile_mod.template_text(4))
    old["schedule"]["jobs"] = [job(every="month")]
    with pytest.raises(ProfileError, match=r"schedule.jobs\[0\].every"):
        profile_mod.validate(old, 4)


@pytest.mark.parametrize("change, field", [
    (lambda d: d["schedule"].update(month_day=31), "schedule.month_day"),
    (lambda d: d["schedule"].update(jobs=[job(every="month", weekday="mon")]), "schedule.jobs[0].weekday"),
    (lambda d: d["refresh"].update(zone=[15, 4]), "refresh.zone"),
    (lambda d: d["refresh"].update(zone=[0, 15]), "refresh.zone"),
    (lambda d: d["refresh"].update(min_change=0), "refresh.min_change"),
    (lambda d: d["refresh"].update(weekly_limit=0), "refresh.weekly_limit"),
    (lambda d: d["ai_visibility"].update(citations="tracker"), "ai_visibility.citations"),
    (lambda d: d["ai_visibility"].update(citations="keysso_tracker"), "ai_visibility.tracker_id"),
    (lambda d: d["ai_visibility"].update(min_score=7), "ai_visibility.min_score"),
    (lambda d: d["ai_visibility"].update(max_quota=-1), "ai_visibility.max_quota"),
    (lambda d: d["ai_visibility"].update(bots=[""]), "ai_visibility.bots"),
    (lambda d: d["behavior"].update(kind="metrika"), "behavior.counter_env"),
    (lambda d: d["behavior"].update(kind="ga4"), "behavior.kind"),
    (lambda d: d["behavior"].update(drop_share=1), "behavior.drop_share"),
    (lambda d: d["behavior"].update(device_ratio=1), "behavior.device_ratio"),
    (lambda d: d["behavior"].update(recheck_days=[28, 14]), "behavior.recheck_days"),
    (lambda d: d["freeze"].update(months=12), "freeze.months"),
    (lambda d: d["freeze"].update(min_months_down=13), "freeze.min_months_down"),
    (lambda d: d["competitors"].update(max_pages=0), "competitors.max_pages"),
    (lambda d: d["field"].update(max=0), "field.max"),
    (lambda d: d.pop("refresh"), "refresh"),
])
def test_semantic_checks_v5(change, field):
    data = yaml.safe_load(profile_mod.template_text())
    change(data)
    with pytest.raises(ProfileError, match=re.escape(field)):
        profile_mod.validate(data)


def test_metrika_kind_with_env_names_is_valid():
    data = yaml.safe_load(profile_mod.template_text())
    data["behavior"].update(kind="metrika", counter_env="METRICA_COUNTER", token_env="METRICA_TOKEN")
    profile_mod.validate(data)
