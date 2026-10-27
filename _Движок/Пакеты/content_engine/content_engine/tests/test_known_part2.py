"""Известные ответы части 2 (Т33.2) и пункты самопроверки части 2."""
import yaml

from content_engine.cli import main
from content_engine.core.instance import Instance
from content_engine.steps import check as check_mod

from .conftest import TODAY, set_profile

SECTION = {"id": "otoplenie-teplicy#1", "text": "Печь, газовый обогреватель и тёплый пол."}
CASES = {"cases": [
    {"name": "судья: раздел вне пятёрки", "kind": "judge", "sections": [SECTION], "expect": "не принят",
     "answer": {"verdict": "closed_by", "section_id": "чужой#3", "quote": "Печь"}},
    {"name": "судья: цитата не из текста", "kind": "judge", "sections": [SECTION], "expect": "не принят",
     "answer": {"verdict": "closed_by", "section_id": "otoplenie-teplicy#1", "quote": "дровяной котёл"}},
    {"name": "судья: верный ответ", "kind": "judge", "sections": [SECTION], "expect": "принят",
     "answer": {"verdict": "closed_by", "section_id": "otoplenie-teplicy#1", "quote": "газовый обогреватель"}},
    {"name": "вычитка: блокер держит черновик", "kind": "review", "expect": "черновик",
     "claims": [{"n": 1, "claim": "норма 30 м³/ч", "type": "norm", "source": "СП", "result": "blocker"}]},
    {"name": "вычитка: блокер убран человеком", "kind": "review", "expect": "пропуск",
     "claims": [{"n": 1, "claim": "норма 30 м³/ч", "type": "norm", "source": "СП", "result": "blocker"}],
     "decisions": {"1": {"who": "Человек", "decision": "remove"}}},
    {"name": "просроченный факт", "kind": "facts", "expect": "черновик", "today": "2026-03-02",
     "text": "Норма — 30 кубометров в час.",
     "fact": {"fact": "норма воздуха", "value": "30", "checked": "2024-01-01", "ttl_days": 365,
              "status": "confirmed"}},
    {"name": "задание писателю без брифа", "kind": "refuse", "action": "writer_without_brief"},
    {"name": "платный вызов без бюджета", "kind": "refuse", "action": "paid_without_budget"},
    {"name": "вычитка с контекстом автора", "kind": "refuse", "action": "factcheck_with_context"},
]}


def test_part2_cases_all_match(project, capsys):
    path = project.parent / "known_answers.part2.yaml"
    path.write_text(yaml.safe_dump(CASES, allow_unicode=True), encoding="utf-8")
    assert main(["known-answers", "--cases", str(path), "--offline", "--profile", str(project)]) == 0
    out = capsys.readouterr().out
    assert f"совпало {len(CASES['cases'])} из {len(CASES['cases'])}" in out


def test_check_shows_part2_items(project):
    set_profile(project, lambda d: (d["models"]["fact_checker"].update(primary=d["models"]["writer"]["primary"]),
                                    d["images"]["providers"].update(illustration="module:adapters/x.py:P")))
    items = {i["item"]: i for i in check_mod.part2_items(Instance(project, today=TODAY))}
    assert items["вычитка фактов"]["light"] == check_mod.YELLOW and "той же моделью" in items["вычитка фактов"]["detail"]
    assert items["картинки"]["light"] == check_mod.YELLOW and "запрещены профилем" in items["картинки"]["detail"]
    assert items["папка выпущенных статей"]["light"] == check_mod.OFF
    assert {"судья", "спрос части 2", "источники выдачи"} <= set(items)
