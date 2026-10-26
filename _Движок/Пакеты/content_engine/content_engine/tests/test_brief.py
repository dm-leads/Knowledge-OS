"""Бриф: задание planner, проверка кодом, утверждение и его снятие (Т19, П26, П27)."""
import json

import pytest
import yaml

from content_engine.cli import main
from content_engine.core import approval, store
from content_engine.core.errors import GateRefused
from content_engine.core.instance import Instance
from content_engine.steps import brief as brief_mod
from content_engine.steps import queue as queue_mod
from content_engine.steps.demand_map import build_map
from content_engine.steps.gate_article import PASS_, check_topic

from .conftest import TODAY, set_profile, write_article
from .test_demand_map import setup
from .test_gate_article import CLEAN

TID = "T0001"
FORMULATIONS = ["посадка чеснока под зиму", "когда сажать чеснок", "как сажать чеснок осенью"]
NOTES = """# Заметки садовода за октябрь

Октябрь — время посадки чеснока. Чеснок под зиму сажают в рыхлую грядку, посадка чеснока идёт по шнуру.
Зубки чеснока заглубляют на две ладони, посадка заканчивается мульчей. Озимый чеснок любит солнце.
"""
GOOD = {"h1": "Посадка чеснока под зиму: сроки и подготовка",
        "title": "Посадка чеснока под зиму: сроки, сорта и подготовка грядки",
        "description": "Когда сажать озимый чеснок, как выбрать сорт и подготовить грядку, чтобы зубки укоренились "
                       "до морозов и дали крупные головки следующим летом.",
        "slug": "posadka-chesnoka-pod-zimu", "angle": "сроки по температуре почвы, а не по календарю",
        "level": "обычный",
        "sections": [{"h2": "Когда сажать", "h3": []}, {"h2": "Как подготовить грядку", "h3": ["Компост"]}],
        "faq": [{"q": "Когда сажать чеснок под зиму?", "from_query": "когда сажать чеснок"},
                {"q": "Как сажать чеснок осенью?", "from_query": "как сажать чеснок осенью"},
                {"q": "В какие сроки посадка чеснока под зиму?", "from_query": "посадка чеснока под зиму"}],
        "author": "Агроном Второй", "links": [], "read_also": [],
        "bridge": {"place": "после раздела о грядке", "share": 0.06,
                   "honesty_limit": "мульча не спасает от вымерзания при посадке в сентябре"},
        "cta_place": "после раздела о грядке обычным блоком"}


@pytest.fixture
def inst(project):
    write_article(project.parent / "texts", "zametki-oktyabr", "Заметки садовода за октябрь", NOTES)
    set_profile(project, lambda d: d["brief"].update(authors=[
        {"name": "Агроном Первый", "role": "агроном", "experience": "15 лет", "directions": ["огород"],
         "month_limit": 1},
        {"name": "Агроном Второй", "role": "агроном", "experience": "8 лет", "directions": ["огород"],
         "month_limit": 4}]))
    instance = setup(project)
    build_map(instance, offline=True)
    instance = Instance(project, today=TODAY)
    queue_mod.save(instance, [{"id": TID, "topic": FORMULATIONS[0], "formulations": FORMULATIONS,
                               "cluster_id": None, "direction": "огород", "weight": 10, "manual_priority": 0,
                               "status": queue_mod.TAKEN, "gate": None, "reservation_id": None, "bet": {},
                               "created": TODAY.isoformat(), "rejected": None, "prefilter": None}])
    store.write_json(instance.brief_dir(TID) / "top.json", "topic_top", [],
                     meta={"serp_as_of": TODAY.isoformat(), "complete": False, "numbers": {}, "gaps": [],
                           "facts": {"status": "не подключено"}})
    # автор «Агроном Первый» уже взял предел месяца
    other = instance.brief_dir("T0009")
    (other / "brief.yaml").write_text(yaml.safe_dump({"author": "Агроном Первый"}, allow_unicode=True),
                                      encoding="utf-8")
    approval.write(instance, "T0009", "Человек")
    return instance


def put(instance, data):
    (instance.brief_dir(TID) / "brief.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_task_names_author_over_limit_next_and_body_candidates(inst):
    out = brief_mod.make_task(inst, TID)
    assert [a["name"] for a in out["authors"]] == ["Агроном Второй"]
    assert any("Агроном Первый" in n and "следующий" in n for n in out["notes"])
    text = (inst.brief_dir(TID) / "task_brief.md").read_text(encoding="utf-8")
    assert "Роль: planner" in text and "посадка чеснока под зиму" in text
    context = json.loads((inst.brief_dir(TID) / "brief_context.json").read_text(encoding="utf-8"))
    slugs = [c["slug"] for c in context["links"]]
    assert "zametki-oktyabr" in slugs                 # близкое тело, далёкий заголовок
    assert "gazon-vesnoj" not in slugs


def test_clean_brief_passes_and_approves(inst, project):
    brief_mod.make_task(inst, TID)
    put(inst, GOOD)
    out = brief_mod.check(inst, TID)
    assert out["errors"] == [], out["errors"]
    assert main(["prepare", "approve", TID, "--profile", str(project), "--who", "Человек"]) == 0
    approval.require(inst, TID)


def test_three_errors_faq_foreign_key_slug(inst):
    brief_mod.make_task(inst, TID)
    bad = json.loads(json.dumps(GOOD))
    bad["faq"][0] = {"q": "Чем укрыть посадки?", "from_query": "когда сажать чеснок"}
    bad["sections"][0]["h2"] = "Отопление теплицы"
    bad["slug"] = "Posadka--Chesnoka"
    put(inst, bad)
    errors = brief_mod.check(inst, TID)["errors"]
    assert any("нет основ своего запроса" in e for e in errors)
    assert any("таргетирует ключ «отопление теплицы»" in e for e in errors)
    assert any(e.startswith("slug") for e in errors)


def test_faq_question_word_and_mark(inst):
    brief_mod.make_task(inst, TID)
    bad = json.loads(json.dumps(GOOD))
    bad["faq"][2] = {"q": "Посадка чеснока под зиму в октябре", "from_query": "посадка чеснока под зиму"}
    put(inst, bad)
    errors = brief_mod.check(inst, TID)["errors"]
    assert any("не с вопросительного слова" in e for e in errors)
    assert any("не кончается знаком вопроса" in e for e in errors)


def test_link_outside_candidates_is_warning(inst):
    brief_mod.make_task(inst, TID)
    data = dict(GOOD, links=["https://example.ru/blog/zametki-oktyabr", "https://example.ru/blog/chuzhaya"])
    put(inst, data)
    out = brief_mod.check(inst, TID)
    assert out["errors"] == []
    assert out["warnings"] == ["links: https://example.ru/blog/chuzhaya — не из кандидатов перелинковки"]


def test_no_honesty_limit_not_approved_and_cta_heading(inst, project):
    brief_mod.make_task(inst, TID)
    put(inst, dict(GOOD, bridge={"place": "в конце", "share": 0.06, "honesty_limit": ""}, cta_place="Когда сажать"))
    errors = brief_mod.check(inst, TID)["errors"]
    assert any("honesty_limit" in e for e in errors) and any("cta_place — заголовок" in e for e in errors)
    assert main(["prepare", "approve", TID, "--profile", str(project), "--who", "Человек"]) == 1
    with pytest.raises(GateRefused, match="не утверждён"):
        approval.require(inst, TID)


def test_edit_after_approval_removes_it_and_article_passes_gate(inst, project):
    brief_mod.make_task(inst, TID)
    put(inst, GOOD)
    brief_mod.approve(inst, TID, "Человек")
    (inst.brief_dir(TID) / "article.md").write_text(CLEAN, encoding="utf-8")
    report = check_topic(inst, TID, offline=True)
    assert report["verdict"] == PASS_, report["reasons"]
    assert report["article_sha256"] and (inst.brief_dir(TID) / "schema.json").is_file()
    assert any(c["check"] == "schema" and c["ok"] for c in report["checks"])
    put(inst, dict(GOOD, angle="другой угол"))
    with pytest.raises(GateRefused, match="изменён после утверждения"):
        approval.require(inst, TID)
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 1


def test_prepare_requires_taken_topic(inst):
    topics = queue_mod.load(inst)
    topics[0]["status"] = queue_mod.GATED
    queue_mod.save(inst, topics)
    with pytest.raises(GateRefused, match="только у темы taken"):
        brief_mod.make_task(inst, TID)
