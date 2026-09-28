from dataclasses import replace
from datetime import date

import pytest

from growth_engine.core.artifacts import (Branch, CycleRoute, DecisionEntry, GoalTree, KnowledgeEntry,
                                          RouteStep, SourceMapEntry, check_secret_names, close_cycle,
                                          validate_route)
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.core.ladders import UnitOfAction
from growth_engine.tests.helpers import JUL, LATER, candidate, concluded, n


def source(**kw):
    base = dict(name="аналитика-x", source_class="C", status="подключён", history_from="2021-04",
                truth_point="заявки и вход в продажи по каналам", probe="analytics/data за 1 день",
                secret_env_names=("API_KEY_X",))
    base.update(kw)
    return SourceMapEntry(**base)


def test_connected_source_needs_probe():
    with pytest.raises(GuardViolation) as e:
        source(probe="")
    assert e.value.guard == 13 and e.value.severity == GuardViolation.COVERAGE


def test_not_connected_source_may_lack_probe():
    assert source(status="есть, нет доступа", probe="").status == "есть, нет доступа"


def test_secret_value_instead_of_name_rejected():
    with pytest.raises(GuardViolation) as e:
        source(secret_env_names=("y0_AgAAAAB4bWx0eHh4eHh4eHh4eHh4eHh4eHh4eHh4eA",))
    assert e.value.guard == 12


# Короткий токен похож на имя переменной — ловится сверкой с именами из файла секретов, без эха значения.
def test_secret_names_checked_against_known_names():
    with pytest.raises(GuardViolation) as e:
        check_secret_names([source(secret_env_names=("sk_live_abc",))], known_env_names={"API_KEY_X"})
    assert e.value.guard == 12 and "sk_live_abc" not in str(e.value)
    check_secret_names([source()], known_env_names={"API_KEY_X"})


# Классы C, D и F закрывают обязательные классы всех шагов таблицы Р2 при главной метрике класса C.
FULL = {"C": "есть", "D": "есть", "F": "есть"}


def route(overrides=None):
    steps = {i: RouteStep(step=i, classes=dict(FULL)) for i in range(1, 11)}
    steps.update(overrides or {})
    return CycleRoute(cycle_id="Ц-1", goal="окно входа в продажи", written_on=date(2026, 9, 14),
                      expected_confidence="средняя", steps=tuple(steps.values()), main_class="C")


CLASS_STATUS = {"C": "подключён", "D": "подключён", "F": "подключён", "E": "есть, нет доступа"}


def test_full_route_accepted():
    validate_route(route(), CLASS_STATUS)


def test_missing_step_rejected():
    full = route()
    short = replace(full, steps=full.steps[:-1])
    with pytest.raises(GuardViolation, match="шаг 10"):
        validate_route(short, CLASS_STATUS)


def test_duplicate_step_rejected():
    full = route()
    doubled = replace(full, steps=full.steps + (RouteStep(step=3, classes={"C": "есть"}),))
    with pytest.raises(GuardViolation, match="дважды"):
        validate_route(doubled, CLASS_STATUS)


def test_have_for_unconnected_class_rejected():
    with pytest.raises(GuardViolation, match="не подключён"):
        validate_route(route({3: RouteStep(step=3, classes={"E": "есть"})}), CLASS_STATUS)


def test_substitute_and_extraction_must_be_named():
    with pytest.raises(GuardViolation, match="заменитель"):
        validate_route(route({3: RouteStep(step=3, classes={"A": "заменитель"})}), CLASS_STATUS)
    with pytest.raises(GuardViolation, match="цены"):
        validate_route(route({3: RouteStep(step=3, classes={"F": "добыча"})}), CLASS_STATUS)


# Оценка из отчёта 13.09: при сентябре ≈605 окно октября даёт 850 только при октябре ≈1 315.
def test_branch_may_carry_the_accepted_unit_of_action():
    """Связь шагов 3 и 6 по канону — единица действия, а не отдельный артефакт.

    Ворота шага 3: «узкое место названо как метрика, а не как единица действия → не принято». Ворота шага 6: «нет
    метрики, факта или единицы действия → брак». Принятое узкое место записывается в ветку дерева цели, и гипотеза
    обязана стоять на той же единице — иначе диагноз и гипотеза живут порознь (дефект ворот 7б).
    """
    unit = UnitOfAction("A", {"url": "/catalog/item/", "element": "форма подбора, шаг 2"})
    branch = Branch("B1", "веб-поток", n("sales_entry", 400, status=Status.ESTIMATE), unit_of_action=unit)
    assert branch.unit_of_action == unit


def test_branch_without_unit_of_action_is_allowed():
    """Поле необязательное: дерево, записанное до шага 3, остаётся законным."""
    assert Branch("B1", "веб-поток", n("sales_entry", 400, status=Status.ESTIMATE)).unit_of_action is None


def test_goal_tree_gap_is_estimate():
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE),
                    branches=(Branch("B1", "веб-поток", n("sales_entry", 400, status=Status.ESTIMATE)),
                              Branch("B2", "поток без визита", n("sales_entry", 300, status=Status.FACT))))
    gap = tree.gap()
    assert gap.value == 615 and gap.status is Status.ESTIMATE and "пересекаться" in gap.missing


def test_goal_tree_foreign_units_rejected():
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE),
                    branches=(Branch("B1", "веб-поток", n("leads", 400, status=Status.ESTIMATE)),))
    with pytest.raises(GuardViolation) as e:
        tree.gap()
    assert e.value.guard == 3


def test_goal_tree_other_window_rejected():
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE),
                    branches=(Branch("B1", "веб-поток", n("sales_entry", 400, period=JUL, status=Status.ESTIMATE)),))
    with pytest.raises(GuardViolation):
        tree.gap()


def test_goal_tree_foreign_unit_rejected():
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE),
                    branches=(Branch("B1", "веб-поток", replace(n("sales_entry", 400, status=Status.ESTIMATE), unit="₽")),))
    with pytest.raises(GuardViolation) as e:
        tree.gap()
    assert e.value.guard == 3


# Ветки по потокам складываются в цель потока-агрегата; ветка-агрегат в цель одного потока — нет.
def test_goal_tree_flows_fit_aggregate_goal_only():
    by_flow = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE),
                       branches=(Branch("B1", "веб-поток", n("sales_entry", 400, flow="web", status=Status.ESTIMATE)),
                                 Branch("B2", "без визита", n("sales_entry", 300, flow="no_visit", status=Status.ESTIMATE))))
    assert by_flow.gap().value == 615
    narrow = GoalTree(goal=n("sales_entry", 100, flow="web", status=Status.ESTIMATE),
                      branches=(Branch("B1", "всё", n("sales_entry", 10, flow="all", status=Status.ESTIMATE)),))
    with pytest.raises(GuardViolation):
        narrow.gap()


def test_route_step_outside_algorithm_rejected():
    with pytest.raises(GuardViolation, match="вне алгоритма"):
        validate_route(route({11: RouteStep(step=11, classes={"C": "есть"})}), CLASS_STATUS)
    validate_route(route({0: RouteStep(step=0, classes={"C": "есть"})}), CLASS_STATUS)


def knowledge(kid="K-001"):
    return KnowledgeEntry(id=kid, statement="шаг 2 формы подбора не барьер", verdict="опровергнуто",
                          on=date(2026, 10, 20), source="C:analytics-x:data", hypothesis_id="H-001")


DECISION = DecisionEntry("Ц-1", "kill H-001", "эффект ниже порога", subtraction="сняли шаг 2 из очереди")


def test_close_cycle_ok():
    close_cycle("Ц-1", [concluded()], [knowledge()], [DECISION])


def test_close_cycle_requires_decision():
    with pytest.raises(GuardViolation) as e:
        close_cycle("Ц-1", [candidate()], [knowledge()], [DECISION])
    assert e.value.guard == 14


def test_close_cycle_requires_knowledge_row():
    with pytest.raises(GuardViolation) as e:
        close_cycle("Ц-1", [concluded(knowledge_row="K-404")], [knowledge()], [DECISION])
    assert e.value.guard == 14


def test_close_cycle_knowledge_must_be_about_that_hypothesis():
    with pytest.raises(GuardViolation) as e:
        close_cycle("Ц-1", [concluded()], [replace(knowledge(), hypothesis_id="H-002")], [DECISION])
    assert e.value.guard == 14


def test_close_cycle_rejects_foreign_hypothesis():
    with pytest.raises(GuardViolation, match="другого цикла"):
        close_cycle("Ц-1", [replace(concluded(), cycle_id="Ц-0")], [knowledge()], [DECISION])


def test_close_cycle_requires_subtraction():
    with pytest.raises(GuardViolation) as e:
        close_cycle("Ц-1", [concluded()], [knowledge()], [replace(DECISION, subtraction=" ")])
    assert e.value.guard == 15


# Ревью Codex этапа 3, п. 2: потолок ветки — из системы цели или из модели, а не из чужой системы.
def test_goal_tree_ceiling_from_other_system_rejected():
    ceiling = n("sales_entry", 400, status=Status.ESTIMATE, source="D:crm-y:leads")
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE), branches=(Branch("B1", "веб-поток", ceiling),))
    with pytest.raises(GuardViolation) as e:
        tree.gap()
    assert e.value.guard == 3


def test_goal_tree_ceiling_from_model_accepted():
    ceiling = n("sales_entry", 400, status=Status.ESTIMATE, source="C:модель:потолок ветки")
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE), branches=(Branch("B1", "веб-поток", ceiling),))
    assert tree.gap().value == 915


# Ветка — часть цели: разрез потолка по каналу допустим.
def test_goal_tree_branch_may_be_a_segment_of_goal():
    ceiling = replace(n("sales_entry", 400, status=Status.ESTIMATE), segment="channel=seo")
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE), branches=(Branch("B1", "SEO", ceiling),))
    assert tree.gap().value == 915


# П3: потолок ветки из другого съёма не вычитается из цели.
def test_goal_tree_branch_of_other_fetch_date_rejected():
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE),
                    branches=(Branch("B1", "веб-поток", n("sales_entry", 400, status=Status.ESTIMATE, as_of=LATER)),))
    with pytest.raises(GuardViolation) as e:
        tree.gap()
    assert e.value.guard == 3
