"""Маршрутизатор: таблица Р2 «шаг → классы», правила формы бизнеса, заменители и уверенность из решений."""
from dataclasses import replace
from datetime import date

import pytest

from growth_engine.core.artifacts import RouteStep, validate_route
from growth_engine.core.errors import GuardViolation
from growth_engine.core.router import (STEP_CLASSES, check_hypothesis_in_route, derive_confidence, required_classes,
                                       route_draft)
from growth_engine.tests.helpers import candidate
from growth_engine.tests.test_artifacts import CLASS_STATUS, route

ALL = {cls: "подключён" for cls in "ABCDEF"}
FORM = {"purchase": "разовая", "ticket": "высокий", "market": "смешанный", "pmf": "сильный",
        "manual_share": "высокая", "deal_cycle": "длинный"}


def draft(task="объём лидов", form=FORM, status=ALL, main="C", sales=False):
    return route_draft("Ц-1", "окно New First SQL", task, form, status, main, date(2026, 9, 14), sales)


def step(r, number):
    return next(s for s in r.steps if s.step == number)


# --- таблица Р2 и проверка маршрута ---

def test_table_covers_algorithm_and_groups_allow_either_class():
    assert set(STEP_CLASSES) == set(range(11))
    assert required_classes(2, "C") == (("A", "C"), ("D",))


def test_main_metric_class_fills_steps_6_9_10():
    assert required_classes(6, "A") == (("A",),) and required_classes(9, "A") == (("A",),)
    assert required_classes(10, "C") == (("C",), ("D",))


def test_step_without_required_class_rejected():
    with pytest.raises(GuardViolation, match="обязательному классу F") as e:
        validate_route(route({4: RouteStep(step=4, classes={"D": "есть"})}), CLASS_STATUS)
    assert e.value.guard == 9


def test_one_class_of_group_is_enough():
    validate_route(route({2: RouteStep(step=2, classes={"A": "есть", "D": "есть"})}),
                   {**CLASS_STATUS, "A": "подключён"})


def test_step_without_main_metric_class_rejected():
    with pytest.raises(GuardViolation, match="шаг 6"):
        validate_route(replace(route(), main_class="A"), CLASS_STATUS)


def test_route_without_main_metric_class_rejected():
    with pytest.raises(GuardViolation, match="главной метрики"):
        validate_route(replace(route(), main_class=""), CLASS_STATUS)


def test_no_data_needs_reason():
    with pytest.raises(GuardViolation, match="без причины"):
        validate_route(route({8: RouteStep(step=8, classes={"F": "нет данных"})}), CLASS_STATUS)
    honest = route({8: RouteStep(step=8, classes={"F": "нет данных"}, no_data_reason="база диалогов не подключена")})
    validate_route(replace(honest, expected_confidence="низкая"), CLASS_STATUS)


def test_declared_confidence_above_decisions_rejected():
    substituted = route({4: RouteStep(step=4, classes={"F": "заменитель", "D": "есть"}, substitute="экспертная оценка")})
    with pytest.raises(GuardViolation, match="решения дают «средняя»"):
        validate_route(replace(substituted, expected_confidence="высокая"), CLASS_STATUS)
    validate_route(substituted, CLASS_STATUS)


def test_unknown_confidence_rejected():
    with pytest.raises(GuardViolation, match="не из"):
        validate_route(replace(route(), expected_confidence="уверенная"), CLASS_STATUS)


def test_confidence_levels_follow_decisions():
    assert derive_confidence(route().steps, "C") == "высокая"
    assert derive_confidence(route({8: RouteStep(step=8, classes={"F": "нет данных"})}).steps, "C") == "низкая"
    assert derive_confidence((), "C") == "низкая"


# --- черновик маршрута ---

def test_draft_with_every_class_connected_is_valid_and_confident():
    r = draft()
    validate_route(r, ALL)
    assert [s.step for s in r.steps] == list(range(1, 11)) and r.expected_confidence == "высокая"
    assert step(r, 1).classes["D"] == "есть" and r.main_class == "C"


def test_missing_required_class_gets_substitute_from_table():
    r = draft(status={**ALL, "F": "нет"})
    assert step(r, 4).classes["F"] == "заменитель" and step(r, 4).substitute == STEP_CLASSES[4].if_missing
    assert "F" not in step(r, 2).classes and r.expected_confidence == "средняя"


def test_class_without_access_is_not_treated_as_available():
    r = draft(status={**ALL, "D": "есть, нет доступа"})
    assert step(r, 1).classes["D"] == "заменитель" and step(r, 1).substitute == STEP_CLASSES[1].if_missing


def test_main_metric_class_not_connected_substitutes_steps_6_9_10():
    r = draft(status={**ALL, "C": "нет"})
    assert all(step(r, n).classes["C"] == "заменитель" for n in (6, 9, 10))
    assert step(r, 6).substitute == STEP_CLASSES[6].if_missing and step(r, 2).classes == {"A": "есть", "D": "есть",
                                                                                         "B": "есть", "F": "есть",
                                                                                         "E": "есть"}


def test_business_form_rules_give_notes_and_sales_substitute():
    r = draft()
    notes = " ".join(r.notes)
    for marker in ("цикл сделки", "ухудшающий эксперимент", "атрибуция", "отложенный замер", "не из глубины данных"):
        assert marker in notes
    assert step(r, 9).classes["D"] == "заменитель" and "RAT" in step(r, 9).substitute
    assert step(r, 9).classes["C"] == "есть"


def test_repeat_purchase_low_ticket_short_cycle_keep_plain_test():
    r = draft(form={**FORM, "purchase": "повторная", "ticket": "низкий", "deal_cycle": "короткий"})
    assert "D" not in step(r, 9).classes and "когортный треугольник" in " ".join(r.notes)


def test_retention_task_with_one_off_purchase_closes_route():
    r = draft(task="возвращаемость")
    assert step(r, 1).classes["D"] == "есть"
    assert all(not s.classes and "не рычаг" in s.skipped_reason for s in r.steps if s.step > 1)


def test_starting_route_adds_useful_classes():
    assert step(draft(), 2).classes["E"] == "есть"
    assert "E" not in step(draft(status={**ALL, "E": "нет"}), 2).classes


@pytest.mark.parametrize("kwargs", [dict(task="рост всего"), dict(form={**FORM, "ticket": "средний"}),
                                    dict(form={k: v for k, v in FORM.items() if k != "deal_cycle"}),
                                    dict(main="G")])
def test_unknown_task_form_or_class_rejected(kwargs):
    with pytest.raises(GuardViolation) as e:
        draft(**kwargs)
    assert e.value.guard == 9


# Ревью 14.09: пропуск шага с обязательными классами — как «нет данных», уверенность не выше низкой.
def test_skipping_required_step_lowers_confidence():
    skipped = route({2: RouteStep(step=2, classes={}, skipped_reason="не нужно")})
    assert derive_confidence(skipped.steps, "C") == "низкая"
    with pytest.raises(GuardViolation, match="решения дают «низкая»"):
        validate_route(skipped, CLASS_STATUS)
    validate_route(replace(skipped, expected_confidence="низкая"), CLASS_STATUS)


def test_skipping_step_without_required_classes_keeps_confidence():
    assert derive_confidence(route({5: RouteStep(step=5, classes={}, skipped_reason="решение по каталогу")}).steps,
                             "C") == "высокая"


def test_closed_route_skips_later_steps_without_penalty():
    r = draft(task="возвращаемость")
    assert r.closed_at == 1 and r.expected_confidence == "высокая"


def test_steps_after_closure_may_not_have_classes():
    with pytest.raises(GuardViolation, match="закрыт на шаге 3"):
        validate_route(replace(route(), closed_at=3), CLASS_STATUS)


# Ревью Codex 15.09: цель-продажи требует класс D на шаге 9 («D для продаж», Р2).
def test_sales_goal_requires_money_class_on_step_9():
    assert required_classes(9, "C", sales_goal=True) == (("C",), ("D",))
    sales_route = replace(route({9: RouteStep(step=9, classes={"C": "есть", "F": "есть"})}), sales_goal=True)
    with pytest.raises(GuardViolation, match="шаг 9: нет решения по обязательному классу D"):
        validate_route(sales_route, CLASS_STATUS)


def test_sales_goal_draft_confidence_follows_money_class_on_step_9():
    plain = {**FORM, "ticket": "низкий", "deal_cycle": "короткий"}
    confident = draft(sales=True, form=plain)
    assert step(confident, 9).classes["D"] == "есть" and confident.expected_confidence == "высокая"
    assert confident.sales_goal and "обязателен класс D" in " ".join(confident.notes)
    assert draft(sales=True).expected_confidence == "средняя"   # высокий чек: продажи в тесте — заменителем
    no_money = draft(sales=True, form=plain, status={**ALL, "D": "нет"})
    assert step(no_money, 9).classes["D"] == "заменитель" and STEP_CLASSES[9].if_missing in step(no_money, 9).substitute


def test_sales_goal_flag_must_be_declared():
    with pytest.raises(GuardViolation) as e:
        draft(sales=None)
    assert e.value.guard == 9


# Ревью Codex 15.09: закрытие маршрута — с причиной, и шаги после него пропускаются только по ней.
def test_closure_needs_reason_shared_by_later_steps():
    later = {i: RouteStep(step=i, classes={}, skipped_reason="не рычаг") for i in range(4, 11)}
    closed = replace(route(later), closed_at=3)
    with pytest.raises(GuardViolation, match="закрыт на шаге 3"):
        validate_route(closed, CLASS_STATUS)
    validate_route(replace(closed, closed_reason="не рычаг"), CLASS_STATUS)
    mixed = replace(closed, closed_reason="не рычаг",
                    steps=closed.steps[:-1] + (RouteStep(step=10, classes={}, skipped_reason="не нужно"),))
    with pytest.raises(GuardViolation, match="закрыт на шаге 3"):
        validate_route(mixed, CLASS_STATUS)
    assert draft(task="возвращаемость").closed_reason


# Ревью Codex 15.09: класс факта-основания гипотезы должен быть перечислен на шаге 6 маршрута её цикла.
def test_hypothesis_fact_class_must_be_listed_on_step_6():
    hypothesis = candidate()   # факт-основание класса C, цикл Ц-1
    check_hypothesis_in_route(route(), hypothesis)
    with pytest.raises(GuardViolation, match="шаге 6") as e:
        check_hypothesis_in_route(route({6: RouteStep(step=6, classes={"F": "есть"})}), hypothesis)
    assert e.value.guard == 9
    with pytest.raises(GuardViolation, match="из цикла"):
        check_hypothesis_in_route(route(), replace(hypothesis, cycle_id="Ц-2"))
