"""Находки проверки кода части 2 (З45): флаги «да» у команд выдачи, платный источник с ценой 0 — ошибка профиля,
квота Keys.so до запросов Wordstat, откат передачи не удаляет файлы, которые правили после передачи."""
import re

import pytest
import yaml

from content_engine.cli import build_parser, main
from content_engine.core import profile as profile_mod
from content_engine.core.errors import ProfileError
from content_engine.core.instance import Instance
from content_engine.steps import records as records_mod

from .conftest import TODAY, set_profile
from .keysso_transport import Response
from .test_demand_part2 import Cloud, wordstat
from .test_findings_121 import _published
from .test_records import released  # noqa: F401
from .test_brief import inst  # noqa: F401
from .test_demand_part2 import keys  # noqa: F401 — имена переменных ключей в окружении теста


@pytest.mark.parametrize("cmd", [["queue", "take", "T1"], ["queue", "retake", "T1"], ["queue", "refill"],
                                 ["gate", "topic", "тема"]])
def test_serp_commands_take_budget_flags(cmd):
    args = build_parser().parse_args(cmd + ["--confirm-spend", "1.5", "--unattended", "--profile", "p.yaml"])
    assert args.confirm_spend == 1.5 and args.unattended is True


@pytest.mark.parametrize("change, field", [
    (lambda d: d["sources"].update(serp_order=["keysso", "yandex_search"]), "sources.yandex_search.price_rub"),
    (lambda d: d["sources"]["wordstat"].update(order=["cloud"]), "sources.wordstat.cloud_price_rub"),
])
def test_paid_source_with_zero_price_is_profile_error(change, field):
    data = yaml.safe_load(profile_mod.template_text())
    change(data)
    with pytest.raises(ProfileError, match=re.escape(field)):
        profile_mod.validate(data)


class Limited(Cloud):
    def __call__(self, method, url, params=None, json=None, headers=None, timeout=None):
        if url.endswith("limits/all"):
            return Response(200, {"apiRequest": {"limit": 100, "usedLimit": 99}})
        return super().__call__(method, url, params=params, json=json, headers=headers, timeout=timeout)


def test_keysso_wordstat_checks_limit_before_requests(project):
    from content_engine.steps.demand import collect
    wordstat(project, ["keysso", "cloud"], price=0.02)
    set_profile(project, lambda d: d["budget"].update(run_limit_rub=10, month_limit_rub=100))
    site = Limited(keysso_status=200)
    report = collect(Instance(project, today=TODAY, transport=site), confirm=1.0)
    item = report["sources"]["wordstat"]
    assert item["source"] == "cloud" and any("остаток лимита API 1" in f for f in item["fallback"])


def test_rollback_keeps_file_edited_after_transfer(released, project):  # noqa: F811
    instance, day = released
    _published(project, "write")
    records_mod.record(Instance(project, today=day), TID_OF(instance), apply=True)
    folder = instance.root / "vypusk" / "posadka-chesnoka-pod-zimu"
    (folder / "article.md").write_text("правка человека", encoding="utf-8")
    assert main(["publish", "rollback", TID_OF(instance), "--apply", "--profile", str(project)]) == 0
    assert (folder / "article.md").read_text(encoding="utf-8") == "правка человека"
    assert not (folder / "schema.json").exists()


def test_rollback_without_right_writes_nothing(released, project, capsys):  # noqa: F811
    """О3: откат передачи при `paths.allow: files` — не в плане, код 1, файлы на месте."""
    from .conftest import set_profile
    instance, day = released
    _published(project, "write")
    records_mod.record(Instance(project, today=day), TID_OF(instance), apply=True)
    folder = instance.root / "vypusk" / "posadka-chesnoka-pod-zimu"
    before = sorted(p.name for p in folder.iterdir())
    set_profile(project, lambda d: d["paths"].update(allow="files"))
    assert main(["publish", "rollback", TID_OF(instance), "--apply", "--profile", str(project)]) == 1
    assert "paths.allow" in capsys.readouterr().out
    assert sorted(p.name for p in folder.iterdir()) == before


def TID_OF(instance):
    from .test_brief import TID
    return TID
