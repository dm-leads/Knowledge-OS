"""Профиль версии 2 и его перевод (Т15, П16): старый профиль не принимается, перевод сохраняет калибровку."""
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from content_engine.cli import main
from content_engine.core import migrate
from content_engine.core import profile as profile_mod
from content_engine.core.errors import ProfileError

from .conftest import FIXTURES

V1 = (FIXTURES / "profile_v1.yaml").read_text(encoding="utf-8")


def calibrated_v1(tmp_path: Path, *, crlf: bool = False) -> Path:
    """Профиль версии 1 с калибровкой проекта: свои пороги, комментарии внутри budget, два направления."""
    text = V1.replace("  body_warn: 25 ", "  body_warn: 30 ")
    text = text.replace("  reserve: 0                          # объектов квоты Keys.so, которые машина не трогает\n",
                        "  # решение владельца: резерв квоты не держим\n"
                        "  reserve: 0                          # объектов квоты Keys.so, которые машина не трогает\n"
                        "  # предел прогона снят: выдача добирается порциями\n")
    text = text.replace("  require_bet: true ", "  require_bet: true ")
    text = text.replace(
        "    quarter_quota: 0                  # тем на квартал; 0 — без квоты\n",
        "    quarter_quota: 0                  # тем на квартал; 0 — без квоты\n"
        "  - name: второе направление        # своё направление проекта\n"
        "    words: [второе, другое]\n"
        "    weight: 2\n"
        "    quarter_quota: 3\n")
    path = tmp_path / "profile.yaml"
    data = text.replace("\n", "\r\n") if crlf else text
    path.write_bytes(data.encode("utf-8"))
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_template_is_version_2_without_required_bet():
    data = yaml.safe_load(profile_mod.template_text(2))
    assert data["schema_version"] == 2
    assert data["queue"]["require_bet"] is False
    profile_mod.validate(data, 2)


def test_v1_profile_refused_by_any_command_with_migrate_hint(tmp_path, capsys):
    path = calibrated_v1(tmp_path)
    assert main(["queue", "status", "--profile", str(path)]) == 3
    err = capsys.readouterr().err
    assert "профиль версии 1" in err and "profile migrate" in err
    assert main(["profile", "check", "--profile", str(path)]) == 3


def test_migrate_without_apply_changes_nothing(tmp_path, capsys):
    path = calibrated_v1(tmp_path)
    before = digest(path)
    assert main(["profile", "migrate", "--profile", str(path)]) == 0
    out = capsys.readouterr().out
    assert digest(path) == before
    assert "раздел models" in out and "budget.run_limit_rub" in out and "directions[1].volume" in out
    assert "schema_version: 1 → 2" in out
    assert "queue.require_bet: оставлено значение проекта (true)" in out
    assert "файл не изменён" in out


def test_migrate_apply_keeps_calibration_and_comments(tmp_path, capsys):
    path = calibrated_v1(tmp_path)
    old = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert main(["profile", "migrate", "--profile", str(path), "--apply"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"записано строк \d+ — прочитано \d+", out)
    assert "повторите `profile migrate`" in out             # перевод — на одну версию за запуск (Т15.3)
    text = path.read_text(encoding="utf-8")
    new = yaml.safe_load(text)
    profile_mod.validate(new, 2)
    assert new["schema_version"] == 2
    assert new["thresholds"]["body_warn"] == 30 and new["queue"]["require_bet"] is True
    assert len(re.findall(r"^budget:", text, re.M)) == 1
    assert "# решение владельца: резерв квоты не держим" in text and "# предел прогона снят" in text
    assert new["budget"] == {"reserve": 0, "max_quota": None, "run_limit_rub": 0, "month_limit_rub": 0}
    assert [d["volume"] for d in new["directions"]] == [[6000, 12000], [6000, 12000]]
    assert new["directions"][1]["words"] == ["второе", "другое"]
    for key, value in old.items():
        if key != "schema_version":
            assert new[key] == value or isinstance(value, (dict, list)), key
    # поля бюджета вставлены внутрь раздела, а не в конец файла
    budget = text.split("budget:", 1)[1].split("\ndirections:", 1)[0]
    assert "run_limit_rub" in budget and "month_limit_rub" in budget


def test_migrate_keeps_crlf(tmp_path):
    path = calibrated_v1(tmp_path, crlf=True)
    migrate.apply(path, migrate.plan(path))
    raw = path.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")
    migrate.apply(path, migrate.plan(path))                  # 2 → 3
    migrate.apply(path, migrate.plan(path))                  # 3 → 4
    migrate.apply(path, migrate.plan(path))                  # 4 → 5
    raw = path.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")
    profile_mod.load(path)


def test_migrate_twice_refused(tmp_path):
    path = calibrated_v1(tmp_path)
    for _ in range(4):                                       # 1 → 2 → 3 → 4 → 5
        migrate.apply(path, migrate.plan(path))
    with pytest.raises(ProfileError, match="уже версии 5"):
        migrate.plan(path)


def test_duplicate_key_is_error():
    with pytest.raises(ProfileError, match="повторный ключ «budget»"):
        migrate.parse("budget:\n  reserve: 0\nbudget:\n  reserve: 1\n")


def test_role_without_fallback_is_profile_error():
    data = yaml.safe_load(profile_mod.template_text())
    data["models"]["writer"]["fallback"] = ""
    with pytest.raises(ProfileError, match="models.writer.fallback"):
        profile_mod.validate(data)
    del data["models"]["judge"]
    with pytest.raises(ProfileError, match="models.judge"):
        profile_mod.validate(data)


@pytest.mark.parametrize("change, field", [
    (lambda d: d["publish"].update(allow="everything"), "publish.allow"),
    (lambda d: d["indexing"].update(allow="publish"), "indexing.allow"),
    (lambda d: d["publish"].update(mode="direct"), "publish.adapter"),
    (lambda d: d["budget"].update(run_limit_rub=500, month_limit_rub=100), "budget.run_limit_rub"),
    (lambda d: d["prepare"].update(serp_max_age_days=7), "prepare.serp_max_age_days"),
    (lambda d: d["assemble"].update(adapter="module:adapters/x.py"), "assemble.adapter"),
    (lambda d: d["directions"][0].update(volume=[9000, 3000]), "directions[0].volume"),
])
def test_semantic_checks(change, field):
    data = yaml.safe_load(profile_mod.template_text())
    change(data)
    with pytest.raises(ProfileError, match=re.escape(field)):
        profile_mod.validate(data)
