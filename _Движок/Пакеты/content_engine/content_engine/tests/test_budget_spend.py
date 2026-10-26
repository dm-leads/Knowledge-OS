"""Роли и задания агенту, бюджет-гейт, учёт расхода (Т17, П20–П22)."""
from datetime import timedelta

import pytest

from content_engine.cli import main
from content_engine.core import budget, spend, tasks
from content_engine.core.errors import BudgetRefused
from content_engine.core.instance import Instance
from content_engine.core.number import FACT, NO_DATA
from content_engine.steps import queue as queue_mod

from .conftest import TODAY, set_profile
from .test_queue import instance  # noqa: F401 — фикстура инстанса с картой спроса и выдачей
from .test_queue import topic


class PaidAdapter:
    """Фейковый платный адаптер: считает вызовы."""

    def __init__(self):
        self.calls = 0

    def make(self):
        self.calls += 1
        return "картинка.png"


def limits(project, run=100, month=300):
    set_profile(project, lambda d: d["budget"].update(run_limit_rub=run, month_limit_rub=month))
    return Instance(project, today=TODAY)


def test_task_names_role_primary_and_fallback(project):
    inst = Instance(project, today=TODAY)
    path = tasks.write_task(inst, "T0001", "planner", "brief", "Тело задания.", result="brief.yaml",
                            schema="h1: str")
    text = path.read_text(encoding="utf-8")
    primary, fallback = inst.p["models"]["planner"]["primary"], inst.p["models"]["planner"]["fallback"]
    from content_engine.core.roles import describe
    assert "Роль: planner" in text and describe(primary) in text and describe(fallback) in text
    assert "выполнить запасной" in text and "tokens" in text and path.name == "task_brief.md"


def test_budget_gate_four_cases(project):
    inst = limits(project)
    paid = PaidAdapter()

    def call(price, **kw):
        return budget.paid_call(inst, "T0001", "картинки", price, paid.make, what="картинки", source="прайс",
                                **kw)

    with pytest.raises(BudgetRefused, match="--confirm-spend 40.00"):
        call(40, confirm=None, unattended=False)
    assert paid.calls == 0
    assert call(40, confirm=40, unattended=False) == "картинка.png"
    assert paid.calls == 1
    rows = spend.rows(inst)
    assert rows[-1]["kind"] == "rub" and rows[-1]["value"] == 40
    with pytest.raises(BudgetRefused, match="выше потолка прогона"):
        call(150, confirm=150, unattended=False)
    with pytest.raises(BudgetRefused, match="--unattended"):
        call(1, confirm=1, unattended=True)
    assert paid.calls == 1


def test_month_limit_counts_spend_journal(project):
    inst = limits(project, run=100, month=120)
    spend.add(inst, "T0009", "картинки", "rub", 90, "оценка", "строка вне машины")
    with pytest.raises(BudgetRefused, match="за месяц уже 90.00"):
        budget.gate(inst, 40, confirm=40, unattended=False, what="картинки")


def test_zero_limits_forbid_any_paid_call(project):
    inst = Instance(project, today=TODAY)
    with pytest.raises(BudgetRefused, match="выше потолка"):
        budget.gate(inst, 0.5, confirm=10, unattended=False, what="эмбеддинги")


def test_budget_refused_exit_code_2(project, monkeypatch):
    import content_engine.steps.spend_cmd as cmd

    def refuse(args):
        raise BudgetRefused("цена 5 ₽ — нужно «да»")
    monkeypatch.setattr(cmd, "run", refuse)
    assert main(["spend", "report", "--profile", str(project)]) == 2


def test_take_spends_keysso_objects_equal_to_client_counter(instance, project):  # noqa: F811
    queue_mod.save(instance, [topic("T0001", topic="посадка чеснока под зиму",
                                    formulations=["посадка чеснока под зиму", "когда сажать чеснок",
                                                  "как сажать чеснок осенью"])])
    serp = instance.serp()
    assert main(["gate", "topic", "--profile", str(project), "--from-queue", "T0001", "--reserve"]) == 0
    asked = []                                   # счётчик клиента: фразы, отправленные источнику выдачи
    real_top = serp.top
    serp.top = lambda phrases, base: (asked.extend(phrases), real_top(phrases, base))[1]
    next_day = Instance(project, today=TODAY + timedelta(days=1), serp=serp)
    assert queue_mod.take(next_day, "T0001")["taken"]
    spent = [r for r in spend.rows(next_day) if r["kind"] == "keysso_objects" and r["id"] == "T0001"]
    assert len(spent) == 1 and spent[0]["value"] == len(asked) == 3
    assert spent[0]["status"] == FACT


def test_agent_tokens_unknown_is_no_data_and_report_by_kind(project):
    inst = Instance(project, today=TODAY)
    spend.add(inst, "T0001", "бриф", "tokens", None, NO_DATA, "работа агента по подписке")
    spend.add(inst, "T0001", "queue take", "keysso_objects", 3, FACT, "счётчик плана выдачи")
    spend.add(inst, "T0001", "картинки", "rub", 35, "оценка", "прайс провайдера")
    with pytest.raises(Exception):
        spend.add(inst, "T0001", "бриф", "tokens", 0, NO_DATA)
    data = spend.report(inst)
    t = data["topics"]["T0001"]
    assert t["tokens"]["no_data"] == 1 and t["tokens"]["value"] == 0
    assert t["keysso_objects"]["value"] == 3 and t["rub"]["value"] == 35 and t["rub"]["estimate"]
    text = spend.render(data)
    assert "нет данных: 1" in text and "35 ₽ (оценка)" in text


def test_latest_alias_and_codex_described_in_task(project):
    """Модели не привязаны к номеру версии: «-latest» раскрывается словами, Codex — вторая независимая модель."""
    inst = Instance(project, today=TODAY)
    path = tasks.write_task(inst, "T0001", "fact_checker", "facts", "Тело.", result="claims.jsonl", schema="x: str")
    text = path.read_text(encoding="utf-8")
    assert "Codex CLI — вторая независимая модель" in text
    assert "claude-sonnet — последняя версия семейства на день работы" in text
