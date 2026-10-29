"""Профиль версии 3 и перевод `2 → 3` (Т15.1, Т33.1): старый профиль не принимается, перевод сохраняет калибровку."""
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from content_engine.cli import main
from content_engine.core import migrate
from content_engine.core import profile as profile_mod
from content_engine.core.errors import ProfileError


def calibrated_v2(tmp_path: Path) -> Path:
    """Профиль версии 2 с калибровкой проекта: свои уровни и комментарий внутри gate_article.levels и assemble.levels."""
    text = profile_mod.template_text(2)
    text = text.replace("    skeleton: warn\n", "    # решение проекта: скелет — блокирующий\n    skeleton: block\n")
    text = text.replace("    required: warn                    # required_elements на месте\n",
                        "    required: block                   # required_elements на месте\n"
                        "    # оглавление конструктора — своё\n")
    text = text.replace("  body_warn: 25 ", "  body_warn: 30 ")
    text = text.replace("  run_limit_rub: 0 ", "  run_limit_rub: 100 ").replace("  month_limit_rub: 0 ",
                                                                             "  month_limit_rub: 1000 ")
    path = tmp_path / "profile.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v3_and_v2_templates_still_valid():
    data = yaml.safe_load(profile_mod.template_text(3))
    assert data["schema_version"] == 3
    profile_mod.validate(data, 3)                                          # схема версии 3 не тронута версией 4
    profile_mod.validate(yaml.safe_load(profile_mod.template_text(2)), 2)   # схема версии 2 не тронута


def test_v2_profile_refused_with_migrate_hint(tmp_path, capsys):
    path = calibrated_v2(tmp_path)
    assert main(["queue", "status", "--profile", str(path)]) == 3
    err = capsys.readouterr().err
    assert "профиль версии 2" in err and "profile migrate" in err


def test_migrate_2_to_3_plan_changes_nothing(tmp_path, capsys):
    path = calibrated_v2(tmp_path)
    before = digest(path)
    assert main(["profile", "migrate", "--profile", str(path)]) == 0
    out = capsys.readouterr().out
    assert digest(path) == before
    assert "версия 2 → 3" in out and "раздел paths" in out and "sources.wordstat" in out
    assert "gate_article.levels.rhythm" in out and "assemble.levels.comments" in out


def test_migrate_2_to_3_keeps_calibration_and_comments(tmp_path, capsys):
    path = calibrated_v2(tmp_path)
    old = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert main(["profile", "migrate", "--profile", str(path), "--apply"]) == 0
    assert re.search(r"записано строк \d+ — прочитано \d+", capsys.readouterr().out)
    text = path.read_text(encoding="utf-8")
    new = yaml.safe_load(text)
    profile_mod.validate(new, 3)
    assert new["schema_version"] == 3
    assert new["gate_article"]["levels"]["skeleton"] == "block"
    assert new["assemble"]["levels"]["required"] == "block" and new["assemble"]["levels"]["comments"] == "block"
    assert new["thresholds"]["body_warn"] == 30
    assert new["budget"]["run_limit_rub"] == 100 and new["budget"]["usd_rub"] == 0
    assert len(re.findall(r"^\s+levels:", text, re.M)) == 2          # gate_article.levels и assemble.levels — по одному
    assert "# решение проекта: скелет — блокирующий" in text and "# оглавление конструктора — своё" in text
    for name in ("rhythm", "facts", "image_alt", "image_credits"):
        assert name in new["gate_article"]["levels"]
    levels = text.split("gate_article:", 1)[1].split("\n  fields:", 1)[0]
    assert "rhythm: warn" in levels                                  # новое поле — внутри своего раздела
    for key, value in old.items():
        if key != "schema_version":
            assert new[key] == value or isinstance(value, (dict, list)), key
    assert new["paths"]["topic_dir"] == "data/briefs/{id}"


def test_v1_reaches_v3_in_two_runs(tmp_path):
    from .conftest import FIXTURES
    path = tmp_path / "profile.yaml"
    path.write_text((FIXTURES / "profile_v1.yaml").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    first = migrate.plan(path)
    assert (first.version_from, first.version_to) == (1, 2)
    migrate.apply(path, first)
    profile_mod.validate(yaml.safe_load(path.read_text(encoding="utf-8")), 2)
    second = migrate.plan(path)
    assert (second.version_from, second.version_to) == (2, 3)
    migrate.apply(path, second)
    profile_mod.validate(yaml.safe_load(path.read_text(encoding="utf-8")), 3)
    migrate.apply(path, migrate.plan(path))                  # 3 → 4
    migrate.apply(path, migrate.plan(path))                  # 4 → 5
    profile_mod.load(path)


@pytest.mark.parametrize("change, field", [
    (lambda d: d["paths"].update(topic_dir="data/briefs"), "paths.topic_dir"),
    (lambda d: d["paths"].update(published_dir="Статьи/{title}"), "paths.published_dir"),
    (lambda d: d["paths"].update(published_dir="C:/Статьи/{slug}"), "paths.published_dir"),
    (lambda d: d["images"]["providers"].update(cover="kie"), "images.providers.cover"),
    (lambda d: d["images"]["providers"].update(product="library"), "images.library.index"),
    (lambda d: d["sources"]["wordstat"].update(order=["wordstat"]), "sources.wordstat.order[0]"),
    (lambda d: d["sources"].update(serp_order=[]), "sources.serp_order"),
    (lambda d: d["judge"]["embeddings"].update(kind="api"), "judge.embeddings.key_env"),
    (lambda d: d["facts"].update(mode="robot"), "facts.mode"),
    (lambda d: d["paths"].update(allow="publish"), "paths.allow"),
    (lambda d: d["images"].update(allow="publish"), "images.allow"),
    (lambda d: d["gate_article"].update(ai_tone_patterns=["(незакрытая"]), "gate_article.ai_tone_patterns"),
    (lambda d: d["gate_article"]["levels"].pop("image_alt"), "gate_article.levels.image_alt"),
])
def test_semantic_checks_v3(change, field):
    data = yaml.safe_load(profile_mod.template_text())
    change(data)
    with pytest.raises(ProfileError, match=re.escape(field)):
        profile_mod.validate(data)
