"""Проверки гейта инстанса в ядре (задача 6.1): строка проверки и код итога, модель последнего снимка, сверка съёмов
одной системы, реестр, семь листов, карта источников."""
from dataclasses import fields, replace
from datetime import date
from pathlib import Path

import pytest

from growth_engine.core.arithmetic import add
from growth_engine.core.artifacts import Branch, DecisionEntry, GoalTree, KnowledgeEntry
from growth_engine.core.economy import Tolerance, money_model, money_share, noise, per_unit
from growth_engine.core.errors import GuardViolation
from growth_engine.core.health import (Check, Outcome, check_artifacts, check_model, check_registry, check_source_map,
                                       check_sources_sheet, compare_takes, summary, verdict, violation)
from growth_engine.core.number import Status
from growth_engine.core.registry import Decision, HStatus, transition
from growth_engine.core.source_map import load_source_map
from growth_engine.core.storage import SHEETS
from growth_engine.tests.helpers import AS_OF, CFG, LATER, candidate, measured, n
from growth_engine.tests.test_measure_and_state import launched

FIXTURES = Path(__file__).parent / "fixtures"
ROLES = dict(users="sales_entry", sales="sales", revenue="pnl_revenue", cogs="pnl_cogs", profit="gross_profit")
TODAY = date(2026, 9, 15)


def rub(metric, value, **kw):
    return replace(n(metric, value, **kw), unit="₽")


def cabinet(scope, users, sales, revenue, cogs, profit):
    return dict(users=n("sales_entry", users, scope=scope), sales=n("sales", sales, scope=scope),
                revenue=rub("pnl_revenue", revenue, scope=scope), cogs=rub("pnl_cogs", cogs, scope=scope),
                profit=rub("gross_profit", profit, scope=scope))


P1 = cabinet("p1", 1860, 554, 67_619_396, 43_039_139, 24_580_257)
P2 = cabinet("p2", 134, 22, 3_000_000, 2_000_000, 1_000_000)


def snapshot(*cabinets):
    """Снимок так, как его пишет прогон модели: числа модели кабинетов, порог шума C1, AMPU и маржа компании."""
    numbers, models = [], []
    for inputs in cabinets:
        model = money_model(**inputs, cfg=CFG)
        models.append(model)
        numbers += [getattr(model, field.name) for field in fields(model)] + [noise(model.c1)]
    if len(models) > 1:
        profit, users, revenue = (add([getattr(m, role) for m in models], CFG) for role in ("profit", "users", "revenue"))
        numbers += [per_unit(profit, users, "ampu", CFG), money_share(profit, revenue, "margin", CFG)]
    return numbers


def failing(checks):
    return [check for check in checks if check.outcome in (Outcome.STRUCTURE, Outcome.COVERAGE)]


def kinds(checks):
    return [(check.guard, check.outcome) for check in checks]


# --- строка проверки и код итога ---

def test_verdict_render_and_summary():
    passed = Check("модель", 13, Outcome.PASSED, "ок")
    skipped = Check("источники", 13, Outcome.SKIPPED, "платная проба")
    coverage = violation("источники", GuardViolation(13, "нет доступа", GuardViolation.COVERAGE))
    assert verdict([]) == 1 and verdict([passed, skipped]) == 0 and verdict([passed, coverage]) == 1
    assert coverage.render() == "🟧 [страж 13] источники: нет доступа"
    assert violation("модель", GuardViolation(4, "смешанная"), "кабинет p1").text == "кабинет p1: смешанная"
    assert summary([passed, skipped, coverage]) == ("ИТОГ: гейт НЕ пройден — пройдено 1, структура 0, покрытие 1, "
                                                    "не проверено 0, пропущено 1")


# --- модель ---

def test_clean_snapshot_reproduces_every_model_number():
    checks = check_model(snapshot(P1, P2), ROLES, CFG, TODAY, 35, "analytics-x")
    assert failing(checks) == [] and verdict(checks) == 0
    assert checks[0].text == "снимок модели от 03.09.2026: 12 дн. при пределе 35"
    assert [check.text.split(":")[0] for check in checks[1:3]] == ["кабинет p1, окно 01.08.2026–31.08.2026",
                                                                    "кабинет p2, окно 01.08.2026–31.08.2026"]
    assert "чисел сверено с пересчётом 6" in checks[1].text
    assert checks[3].text.startswith("компания ") and "чисел сверено с пересчётом 2" in checks[3].text


def test_substituted_profit_breaks_the_pnl_identity():
    numbers = [replace(x, value=x.value + 1000) if (x.metric, x.scope) == ("gross_profit", "p1") else x
               for x in snapshot(P1, P2)]
    bad = failing(check_model(numbers, ROLES, CFG, TODAY, 35, "analytics-x"))
    assert kinds(bad) == [(2, Outcome.STRUCTURE)] and bad[0].text.startswith("кабинет p1")
    assert "себестоимость" in bad[0].text


def test_stored_derived_number_the_code_does_not_reproduce_stops():
    numbers = [replace(x, value=x.value * 1.01) if (x.metric, x.scope) == ("c1", "p2") else x for x in snapshot(P1, P2)]
    bad = failing(check_model(numbers, ROLES, CFG, TODAY, 35, "analytics-x"))
    assert kinds(bad) == [(2, Outcome.STRUCTURE)] and "c1 — в снимке" in bad[0].text


# Числа других систем в снимок не входят (ревью Codex этапа 6, п. 4) — C1 из двух систем из хранилища не собрать.
# Смешанная природа внутри денежной системы — деньги одного кабинета в разных валютах — стоп стражем 4.
def test_money_of_one_cabinet_in_two_currencies_stops_with_guard_4():
    numbers = [replace(x, unit="USD") if (x.metric, x.scope) == ("gross_profit", "p1") else x for x in snapshot(P1)]
    bad = failing(check_model(numbers, ROLES, CFG, TODAY, 35, "analytics-x"))
    assert kinds(bad) == [(4, Outcome.STRUCTURE)] and bad[0].text.startswith("кабинет p1")


def test_numbers_of_another_system_do_not_enter_the_snapshot():
    foreign = n("sales_entry", 1860, scope="p1", source="D:crm-y:deals", as_of=LATER)
    checks = check_model(snapshot(P1) + [foreign], ROLES, CFG, TODAY, 35, "analytics-x")
    assert failing(checks) == [] and checks[0].text.startswith("снимок модели от 03.09.2026")


def test_snapshot_without_numbers_the_model_run_writes_is_coverage():
    numbers = [x for x in snapshot(P1) if x.metric not in ("c1", "ampu")]
    bad = failing(check_model(numbers, ROLES, CFG, TODAY, 35, "analytics-x"))
    assert kinds(bad) == [(13, Outcome.COVERAGE)] and "в снимке нет чисел, которые пишет прогон модели: c1, ampu" in bad[0].text


def test_old_snapshot_is_coverage():
    bad = failing(check_model(snapshot(P1), ROLES, CFG, date(2026, 10, 20), 35, "analytics-x"))
    assert kinds(bad) == [(13, Outcome.COVERAGE)] and "47 дн. при пределе 35" in bad[0].text


def test_no_snapshot_is_coverage_not_clean():
    checks = check_model([], ROLES, CFG, TODAY, 35, "analytics-x")
    assert kinds(checks) == [(13, Outcome.COVERAGE)] and verdict(checks) == 1


def test_only_the_latest_take_is_checked():
    older = [replace(x, as_of=date(2026, 9, 1), value=x.value + (5000 if x.metric == "gross_profit" else 0))
             for x in snapshot(P1)]
    checks = check_model(older + snapshot(P1), ROLES, CFG, TODAY, 35, "analytics-x")
    assert failing(checks) == [] and checks[0].text.startswith("снимок модели от 03.09.2026")


def test_model_group_without_a_base_number_is_coverage():
    numbers = [x for x in snapshot(P1) if x.metric != "pnl_cogs"]
    bad = failing(check_model(numbers, ROLES, CFG, TODAY, 35, "analytics-x"))
    assert kinds(bad) == [(13, Outcome.COVERAGE)] and "cogs (pnl_cogs)" in bad[0].text


# Лист «Модель — снимки» хранит и числа цикла: цели и потолки дерева, эффекты гипотез (система «модель»). Они не
# подменяют базовые числа модели и не прячут возраст снимка.
def test_numbers_built_by_the_cycle_model_do_not_enter_the_snapshot():
    later_tree = [replace(x, as_of=LATER) for x in (TREE.goal, TREE.branches[0].ceiling)]
    same_day_effect = rub("gross_profit", 180_000, status=Status.ESTIMATE, source="D:модель:эффект гипотезы")
    checks = check_model(snapshot(P1) + later_tree + [same_day_effect], ROLES, CFG, TODAY, 35, "analytics-x")
    assert failing(checks) == [] and checks[0].text.startswith("снимок модели от 03.09.2026")


def test_two_different_numbers_of_one_role_in_one_window_stop():
    other = n("sales_entry", 1900, scope="p1", source="C:analytics-x:другой запрос")
    bad = failing(check_model(snapshot(P1) + [other], ROLES, CFG, TODAY, 35, "analytics-x"))
    assert kinds(bad) == [(13, Outcome.STRUCTURE)] and "неоднозначен" in bad[0].text


# --- пересмотры между съёмами ---

EARLIER = n("sales_entry", 587, as_of=AS_OF)
TOLERANCE = Tolerance(2, 0.01)


@pytest.mark.parametrize("later_value, outcome", [(589, Outcome.PASSED), (592, Outcome.PASSED), (600, Outcome.COVERAGE)])
def test_shift_between_takes_of_one_system(later_value, outcome):
    check = compare_takes(EARLIER, replace(EARLIER, value=later_value, as_of=LATER), TOLERANCE)
    assert (check.guard, check.outcome) == (13, outcome) and "съём 03.09.2026 — 587, съём 14.09.2026" in check.text


@pytest.mark.parametrize("later, marker", [
    (replace(EARLIER, value=590, as_of=LATER, source="D:crm-y:deals"), "система"),
    (replace(EARLIER, value=590, as_of=LATER, scope="p2"), "кабинет"),
    (replace(EARLIER, value=590), "позже"),
])
def test_different_quantities_or_order_of_takes_stop(later, marker):
    check = compare_takes(EARLIER, later, TOLERANCE)
    assert (check.guard, check.outcome) == (4, Outcome.STRUCTURE) and marker in check.text


def test_take_without_data_is_coverage():
    later = replace(EARLIER, value=None, status=Status.NO_DATA, as_of=LATER, missing="источник не ответил")
    check = compare_takes(EARLIER, later, TOLERANCE)
    assert (check.guard, check.outcome) == (13, Outcome.COVERAGE) and "нет данных для сравнения" in check.text


# --- реестр ---

TREE = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE, source="C:модель:цель окна"),
                branches=(Branch("B1", "веб-поток", n("sales_entry", 400, flow="web", status=Status.ESTIMATE,
                                                      source="C:модель:потолок ветки")),))
DECISION = DecisionEntry("Ц-1", "H-002 → kill", "эффект ниже шума", subtraction="гипотеза H-002 снята", id="D-1")
KNOWLEDGE = KnowledgeEntry(id="K-001", statement="шаг 2 формы подбора не барьер", verdict="опровергнуто",
                           on=date(2026, 10, 20), source="C:analytics-x:data", hypothesis_id="H-001")


def registry(*hypotheses, knowledge=(), decisions=(DECISION,), trees=(TREE,), today=TODAY):
    return check_registry(hypotheses, knowledge, decisions, trees, [], {}, today)


def deferred():
    return transition(launched(), HStatus.DEFERRED, deferred_until=date(2026, 11, 15))


def test_clean_registry_gives_one_passed_line():
    checks = registry(candidate("H-001"), candidate("H-002"))
    assert kinds(checks) == [(13, Outcome.PASSED)] and checks[0].text.startswith("гипотез 2, строк знаний 0, решений 1")


def test_overdue_deferred_measurement_is_coverage():
    assert failing(registry(deferred(), today=date(2026, 11, 15))) == []
    bad = failing(registry(deferred(), today=date(2026, 11, 16)))
    assert kinds(bad) == [(13, Outcome.COVERAGE)] and "назначен на 15.11.2026" in bad[0].text


def test_ended_test_window_without_measurement_is_coverage():
    assert failing(registry(launched(), today=date(2026, 10, 18))) == []
    bad = failing(registry(launched(), today=date(2026, 10, 19)))
    assert kinds(bad) == [(13, Outcome.COVERAGE)] and "окно теста закончилось 17.10.2026" in bad[0].text


def test_conclusion_needs_its_own_knowledge_row():
    concluded = transition(measured(), HStatus.CONCLUDED, decision=Decision.KILL, conclusion="не сработало",
                           knowledge_row="K-001")
    assert failing(registry(concluded, knowledge=[KNOWLEDGE])) == []
    assert kinds(failing(registry(concluded))) == [(14, Outcome.STRUCTURE)]
    assert kinds(failing(registry(concluded, knowledge=[replace(KNOWLEDGE, hypothesis_id="H-009")]))) == \
        [(14, Outcome.STRUCTURE)]


def test_decision_without_subtraction_stops_with_15():
    bad = failing(registry(candidate("H-001"), decisions=[replace(DECISION, subtraction="  ")]))
    assert kinds(bad) == [(15, Outcome.STRUCTURE)] and "D-1" in bad[0].text


def test_broken_card_is_reported_and_other_hypotheses_are_still_checked():
    bad = failing(registry(replace(candidate("H-002"), owner=""), deferred(), today=date(2026, 11, 16)))
    assert kinds(bad) == [(6, Outcome.STRUCTURE), (13, Outcome.COVERAGE)] and "H-002" in bad[0].text


def test_branch_absent_from_goal_tree_is_coverage():
    bad = failing(registry(candidate("H-001"), trees=()))
    assert kinds(bad) == [(13, Outcome.COVERAGE)] and "«B1»" in bad[0].text


# --- семь листов и карта источников ---

def test_empty_sheet_is_not_a_violation_missing_sheet_is_coverage():
    sheets = {name: 0 for name in SHEETS.values()}
    checks = check_artifacts({**sheets, SHEETS["hypotheses"]: 7})
    assert kinds(checks) == [(13, Outcome.PASSED)] and checks[0].text.startswith("листов 7:")
    bad = check_artifacts({**sheets, SHEETS["knowledge"]: None})
    assert [(c.guard, c.outcome, c.text) for c in bad] == [(13, Outcome.COVERAGE,
                                                             "листа «Карта знаний» нет — артефакт не заведён")]


MAP = load_source_map(FIXTURES / "source_map_minimal.yaml")
ENV = ("API_KEY_X", "WEB_TOKEN_Y")


def test_source_map_matches_config_classes_and_secret_names():
    checks = check_source_map(MAP, {"analytics-x": "C", "dialogs-z": "F"}, ENV)
    assert kinds(checks) == [(12, Outcome.PASSED)] and "подключены классы C, F" in checks[0].text


@pytest.mark.parametrize("configured, env, expected, marker", [
    ({"analytics-x": "C", "dialogs-z": "F", "web-y": "A"}, ENV, [(13, Outcome.COVERAGE)], "«web-y» класса A"),
    ({"analytics-x": "C"}, ENV, [(13, Outcome.COVERAGE)], "класс F подключён в карте"),
    ({"analytics-x": "C", "dialogs-z": "F"}, ("API_KEY_X",), [(12, Outcome.STRUCTURE)], "веб-аналитика-y"),
])
def test_source_map_mismatch(configured, env, expected, marker):
    checks = check_source_map(MAP, configured, env)
    assert kinds(checks) == expected and marker in checks[0].text


def test_sources_sheet_behind_the_file_is_coverage():
    assert check_sources_sheet(MAP, MAP).outcome is Outcome.PASSED
    check = check_sources_sheet(MAP, [replace(MAP[0], truth_point="заявки по страницам")] + list(MAP[1:]))
    assert (check.guard, check.outcome) == (13, Outcome.COVERAGE) and "аналитика-x" in check.text
