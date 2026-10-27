"""Текст, критика, вычитка фактов, библиотека фактов, профиль ниши (Т28–Т29, П41–П44)."""
import json
from datetime import timedelta

import pytest

from content_engine.cli import main
from content_engine.core import facts
from content_engine.core.errors import GateRefused
from content_engine.core.instance import Instance
from content_engine.steps import review, write
from content_engine.steps.gate_article import DRAFT, PASS_, check_article, check_topic

from .conftest import TODAY, set_profile
from .test_brief import GOOD, TID, inst, put  # noqa: F401
from .test_gate_article import CLEAN
from .test_schema_assemble import ready_topic

ALIVE = {"link_checker": lambda url, timeout: (True, "200")}


def by_check(report):
    return {c["check"]: c for c in report["checks"]}


# --- Т28: задания и проверки текста ------------------------------------------------------------------------------

def test_write_task_refused_without_approved_brief(inst, project):  # noqa: F811
    from content_engine.steps import brief as brief_mod
    brief_mod.make_task(inst, TID)
    put(inst, GOOD)
    assert main(["write", "task", TID, "--profile", str(project)]) == 1
    with pytest.raises(GateRefused, match="не утверждён|утвержд"):
        write.make_tasks(inst, TID)


def test_three_role_tasks_and_one_critique_task(inst, project):  # noqa: F811
    ready_topic(inst)
    out = write.make_tasks(inst, TID)
    names = [p.split("\\")[-1].split("/")[-1] for p in out["tasks"]]
    assert names == ["task_plan.md", "task_write.md", "task_critique.md"]
    folder = inst.brief_dir(TID)
    roles = {n: (folder / n).read_text(encoding="utf-8") for n in names}
    assert "Роль: planner" in roles["task_plan.md"] and "Роль: writer" in roles["task_write.md"]
    assert "Роль: critic" in roles["task_critique.md"] and "запасная" in roles["task_write.md"]
    assert any("ставки нет" in n for n in out["notes"])
    def body(text):
        return [line for line in text.splitlines() if not line.startswith("- Составлено:")]
    before = body(roles["task_critique.md"])
    assert main(["review", "task", TID, "--profile", str(project)]) == 0
    assert body((folder / "task_critique.md").read_text(encoding="utf-8")) == before   # одно задание на обе команды
    assert not (folder / "task_critic.md").exists()


def test_strong_level_needs_fact_pack(inst):  # noqa: F811
    ready_topic(inst)
    brief = dict(GOOD, level="усиленный")
    put(inst, brief)
    from content_engine.steps import brief as brief_mod
    brief_mod.approve(inst, TID, "Человек")
    with pytest.raises(GateRefused, match="пакета проверенных фактов"):
        write.make_tasks(inst, TID)
    facts.add(inst, [{"fact": "чеснок сажают за 3 недели до морозов", "value": "3", "unit": "недели",
                      "source": "https://example.org/agro", "checked": TODAY.isoformat(), "ttl_days": 365,
                      "status": "confirmed", "topic": TID}])
    assert write.make_tasks(inst, TID)["level"] == "усиленный"


FLAT = "Это абзац ровно из десяти слов про чеснок и грядку осенью."


def article(tmp_path, body):
    path = tmp_path / "a.md"
    path.write_text(CLEAN.split("# Посадка", 1)[0] + body, encoding="utf-8")
    return path


def test_text_checks_find_problems(inst, project, tmp_path):  # noqa: F811
    set_profile(project, lambda d: (d["gate_article"].update(ai_tone_patterns=["в современном мире"]),
                                    d["directions"].append({"name": "огород", "words": ["чеснок"], "weight": 1,
                                                            "quarter_quota": 0, "volume": [100, 300]})))
    instance = Instance(project, today=TODAY)
    long_first = " ".join(["Подробный рассказ о том, что бывает с почвой и погодой."] * 9)
    body = ("# Посадка чеснока под зиму\n\n" + "\n\n".join([FLAT] * 6) + "\n\n## Когда сажать чеснок\n\n"
            + long_first + "\n\n## Как подготовить грядку\n\nВ современном мире грядку готовят по СП 60.13330 заранее."
            "\n\nСм. [статью](https://example.ru/blog/net-takoy-stati).\n\n## Частые вопросы\n\n**Когда?**\nОсенью.\n")
    report = check_article(instance, article(tmp_path, body), direction="огород", offline=True)
    c = by_check(report)
    flat_body = ("# Посадка чеснока под зиму\n\n" + "\n\n".join([FLAT] * 8)
                 + "\n\n## Частые вопросы\n\n**Когда?**\nОсенью.\n")
    flat =check_article(instance, article(tmp_path, flat_body), offline=True)
    assert not by_check(flat)["rhythm"]["ok"]
    assert not c["ai_tone"]["ok"] and "в современном мире" in c["ai_tone"]["detail"].lower()
    assert not c["volume"]["ok"] and "100–300" in c["volume"]["detail"]
    assert not c["norm_links"]["ok"] and not c["internal_links"]["ok"] and "net-takoy-stati" in c["internal_links"]["detail"]
    assert not c["ai_answers"]["ok"] and "Когда сажать чеснок" in c["ai_answers"]["detail"]
    report = check_article(instance, article(tmp_path, body), direction=None, offline=True)
    assert "не проверялось" in by_check(report)["volume"]["detail"]


# --- Т29: вычитка без контекста автора, блокер, решения человека, библиотека, ниша ----------------------------------

def claims(instance, rows):
    folder = review.factcheck_dir(instance, TID)
    (folder / "claims.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                                         encoding="utf-8")


def test_factcheck_task_has_no_author_context(inst):  # noqa: F811
    ready_topic(inst)
    path = review.factcheck_task(inst, TID)
    text = path.read_text(encoding="utf-8")
    assert path.parent == inst.root / "data" / "factcheck" / TID
    for word in ("brief.yaml", "task_write", "brief_context", str(inst.brief_dir(TID))):
        assert word not in text
    assert "Озимый чеснок сажают осенью" in text and "Роль: fact_checker" in text and "codex" in text.lower()
    (inst.brief_dir(TID) / "article.md").write_text(CLEAN + "\nСм. brief.yaml\n", encoding="utf-8")
    with pytest.raises(Exception, match="контексту автора"):
        review.factcheck_task(inst, TID)


def test_blocker_unverified_and_legal_risk_keep_draft_until_human(inst, project):  # noqa: F811
    ready_topic(inst)
    set_profile(project, lambda d: d["facts"].update(enabled=True))
    instance = Instance(project, today=TODAY)
    assert check_topic(instance, TID, **ALIVE)["verdict"] == DRAFT          # вычитки нет — черновик
    review.factcheck_task(instance, TID)
    claims(instance, [{"n": 1, "claim": "за 3 недели до морозов", "type": "number", "source": "https://example.org/a",
                       "result": "blocker"},
                      {"n": 2, "claim": "мульча спасает", "type": "product", "source": "", "result": "unverified"},
                      {"n": 3, "claim": "гарантированный урожай", "type": "product", "source": "https://example.org/b",
                       "result": "confirmed", "legal_risk": True}])
    out = review.check(instance, TID)
    assert not out["clean"] and len(out["open"]) == 3
    assert check_topic(instance, TID, **ALIVE)["verdict"] == DRAFT
    review.accept(instance, TID, 1, "Человек", "remove")
    review.accept(instance, TID, 2, "Человек", "keep")
    review.accept(instance, TID, 3, "Человек", "keep")
    assert review.check(instance, TID)["clean"]
    assert check_topic(instance, TID, **ALIVE)["verdict"] == PASS_
    assert facts.read(instance)[0]["fact"] == "гарантированный урожай"           # подтверждённое — в библиотеку
    review.check(instance, TID)
    assert len(facts.read(instance)) == 1                                          # без дублей


def test_expired_fact_keeps_draft(inst, project):  # noqa: F811
    ready_topic(inst)
    set_profile(project, lambda d: d["facts"].update(enabled=True))
    instance = Instance(project, today=TODAY)
    review.factcheck_task(instance, TID)
    claims(instance, [])
    review.check(instance, TID)
    facts.add(instance, [{"fact": "недель до морозов", "value": "3", "unit": "", "source": "https://example.org/c",
                          "checked": (TODAY - timedelta(days=400)).isoformat(), "ttl_days": 365,
                          "status": "confirmed", "topic": "T0002"}])
    (instance.brief_dir(TID) / "article.md").write_text(CLEAN.replace("за три-четыре недели", "за 3 недели"),
                                                       encoding="utf-8")
    review.factcheck_task(instance, TID)
    claims(instance, [])                                   # новый круг — прежние claims в архиве
    review.check(instance, TID)
    report = check_topic(instance, TID, **ALIVE)
    assert report["verdict"] == DRAFT and "просрочен" in by_check(report)["facts"]["detail"]


def test_old_claims_do_not_close_new_text(inst, project):  # noqa: F811
    """Б1 проверки кода: правка текста → новый круг вычитки; прежние claims и решения его не закрывают."""
    ready_topic(inst)
    set_profile(project, lambda d: d["facts"].update(enabled=True))
    instance = Instance(project, today=TODAY)
    review.factcheck_task(instance, TID)
    claims(instance, [{"n": 1, "claim": "чеснок сажают осенью", "type": "product", "source": "https://example.org/a",
                       "result": "confirmed"}])
    assert review.check(instance, TID)["clean"]
    article = instance.brief_dir(TID) / "article.md"
    article.write_text(article.read_text(encoding="utf-8") + "\nУрожай вырастет вдвое.\n", encoding="utf-8")
    assert not review.check(instance, TID)["clean"]                       # текст изменился после вычитки
    review.factcheck_task(instance, TID)                                  # новый круг
    with pytest.raises(GateRefused, match="вычитки нет"):
        review.check(instance, TID)                                       # старые claims ушли в архив
    assert list((review.factcheck_dir(instance, TID) / "archive").glob("*/claims.jsonl"))
    assert check_topic(instance, TID, **ALIVE)["verdict"] == DRAFT


def test_remove_needs_claim_gone_from_text(inst, project):  # noqa: F811
    ready_topic(inst)
    instance = Instance(project, today=TODAY)
    review.factcheck_task(instance, TID)
    claims(instance, [{"n": 1, "claim": "Озимый чеснок сажают осенью", "type": "product", "source": "",
                       "result": "blocker"}])
    review.accept(instance, TID, 1, "Человек", "remove")
    out = review.check(instance, TID)
    assert not out["clean"] and "ещё в тексте" in out["open"][0]


def test_niche_disclaimer(inst, project):  # noqa: F811
    ready_topic(inst)
    set_profile(project, lambda d: d["niche"].update(kind="medicine", disclaimer="не заменяет консультацию врача"))
    assert check_topic(Instance(project, today=TODAY), TID, **ALIVE)["verdict"] == DRAFT
    set_profile(project, lambda d: d["niche"].update(kind="general", disclaimer=""))
    assert check_topic(Instance(project, today=TODAY), TID, **ALIVE)["verdict"] == PASS_
