"""Совет по темпу и приёмка запуска (Т32, П47)."""
import hashlib
from datetime import timedelta

import yaml

from content_engine.cli import main
from content_engine.core import publications
from content_engine.core.instance import Instance
from content_engine.steps import onboard

from .conftest import TODAY, set_profile


def test_new_domain_fast_rhythm_warned_and_speed_from_index_status(project):
    set_profile(project, lambda d: (d["queue"]["pace"].update(domain_since=(TODAY - timedelta(days=30)).isoformat(),
                                                              new_domain_max_per_day=0.3),
                                    d["queue"].update(per_day=1.0)))
    instance = Instance(project, today=TODAY)
    publications.log(instance, "index_status", id="T1", url="example.ru/blog/a", age=7, google_in=True,
                     yandex_in=False)
    publications.log(instance, "index_status", id="T2", url="example.ru/blog/b", age=8, google_in=False,
                     yandex_in=False)
    out = onboard.pace(instance)
    assert "молодой" in out["warning"] and out["domain_age_days"] == 30
    assert out["indexing_speed"] == "1 из 2 новых адресов в индексе к 7-му дню"
    set_profile(project, lambda d: d["queue"].update(per_day=0.2))
    assert onboard.pace(Instance(project, today=TODAY))["warning"] == ""
    set_profile(project, lambda d: d["queue"]["pace"].update(domain_since=""))
    assert "не установлен" in onboard.pace(Instance(project, today=TODAY))["domain_age"]


def test_onboard_asks_two_decisions_and_writes_only_with_answers(project, capsys):
    before = hashlib.sha256(project.read_bytes()).hexdigest()
    assert main(["onboard", "--profile", str(project)]) == 2
    out = capsys.readouterr().out
    assert "Режим вычитки" in out and "Ритм выпуска" in out
    assert "спрос: не подключено" in out                       # Wordstat нет — не «годна»
    assert "светофор:" in out and "очередь:" in out
    assert hashlib.sha256(project.read_bytes()).hexdigest() == before
    assert main(["onboard", "--answer", "vychitka=human", "--answer", "rhythm=0,43", "--profile", str(project)]) == 0
    data = yaml.safe_load(project.read_text(encoding="utf-8"))
    assert data["facts"]["mode"] == "human" and data["queue"]["per_day"] == 0.43
    assert "записано в профиль: facts.mode = human" in capsys.readouterr().out


def test_niche_numbers_carry_status(project):
    rows = onboard.niche_fitness(Instance(project, today=TODAY))
    assert {r["what"] for r in rows} == {"спрос", "кто в топе", "цена заявки", "регион"}
    assert all(r["status"] in ("факт", "оценка", "proxy", "нет данных") for r in rows)
    assert all(str(r["why"]).strip() for r in rows)           # у каждого числа — источник или причина
