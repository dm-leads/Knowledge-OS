"""Самопроверка (Т14.1, П12) и известные ответы (Т14.2, Т14.3)."""
import yaml

from content_engine.adapters.fake import FakeSerp
from content_engine.cli import main
from content_engine.core.errors import AccessDenied
from content_engine.core.instance import Instance
from content_engine.steps.check import GREEN, OFF, RED, run_check
from content_engine.steps.demand_map import build_map
from content_engine.steps.known_answers import audit

from .conftest import TODAY, set_profile
from .test_demand_map import setup
from .test_layers import SERP


class RevokedSerp:
    name = "отозван"

    def remaining(self):
        raise AccessDenied("Keys.so: HTTP 401 — доступ отозван или ключ сменили")


def test_revoked_key_red_others_collected(project, monkeypatch):
    monkeypatch.setenv("KEYSO_API_KEY", "заведомо-неверный")
    set_profile(project, lambda d: d["sources"]["keysso"].update(enabled=True))
    items = {i["item"]: i for i in run_check(Instance(project, today=TODAY, serp=RevokedSerp()))}
    assert items["Keys.so"]["light"] == RED and "доступ отозван" in items["Keys.so"]["detail"]
    assert items["корпус"]["light"] == GREEN and "страниц 4" in items["корпус"]["detail"]
    assert items["Search Console"]["light"] == OFF and items["Search Console"]["hint"]
    assert items["секрет keysso"]["light"] == GREEN


def write_cases(project, cases):
    (project.parent / "known_answers.yaml").write_text(yaml.safe_dump({"cases": cases}, allow_unicode=True),
                                                       encoding="utf-8")


def prepare(project, monkeypatch):
    setup(project)
    serp = FakeSerp(SERP)
    build_map(Instance(project, today=TODAY, serp=serp))
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, today=TODAY, serp=serp))


BET = {"hypothesis": "h", "expectation": "e", "fail_threshold": "f"}
CASES = [
    {"name": "раздел чужой статьи", "kind": "gate", "topic": "полив теплицы зимой",
     "formulations": ["полив теплицы зимой", "как поливать теплицу зимой", "вода для полива теплицы"],
     "expect": {"verdict": "пересекается", "layer": 2, "page": "otoplenie-teplicy"}},
    {"name": "живые на одном спросе", "kind": "pair", "pages": ["otoplenie-teplicy", "gazon-vesnoj"]},
    {"name": "свободная тема", "kind": "gate", "topic": "посадка чеснока под зиму", "bet": BET,
     "formulations": ["посадка чеснока под зиму", "когда сажать чеснок", "как сажать чеснок осенью"],
     "expect": {"verdict": "свободно"}},
]


def test_known_answers_all_match(project, monkeypatch, capsys):
    prepare(project, monkeypatch)
    write_cases(project, CASES)
    assert main(["known-answers", "--profile", str(project)]) == 0
    assert "совпало 3 из 3" in capsys.readouterr().out


def test_one_mismatch_exit_one(project, monkeypatch, capsys):
    prepare(project, monkeypatch)
    wrong = dict(CASES[2], expect={"verdict": "пересекается"})
    write_cases(project, CASES[:2] + [wrong])
    assert main(["known-answers", "--profile", str(project)]) == 1
    assert "совпало 2 из 3" in capsys.readouterr().out


def test_audit_false_alarm_share(tmp_path):
    path = tmp_path / "decisions.csv"
    path.write_text("тема;вердикт;слой;ложная тревога (да/нет)\nа;пересекается;2;да\nб;спорно;4;нет\n"
                    "в;пересекается;1;нет\nг;свободно;;нет\nд;пересекается;2;\n", encoding="utf-8")
    out = audit(path)
    assert out == {"sample": 5, "marked": 4, "alarms": 3, "false": 1, "share": 0.333}


def test_pending_case_until_date(project, monkeypatch, capsys):
    prepare(project, monkeypatch)
    wrong = dict(CASES[2], expect={"verdict": "пересекается"}, pending_until="2026-03-10",
                 pending_reason="статья вышла вчера")
    write_cases(project, CASES[:2] + [wrong])
    assert main(["known-answers", "--profile", str(project)]) == 0
    assert "совпало 2 из 2, отложено 1" in capsys.readouterr().out
    wrong["pending_until"] = "2026-03-01"                  # срок прошёл — случай снова обязателен
    write_cases(project, CASES[:2] + [wrong])
    assert main(["known-answers", "--profile", str(project)]) == 1
