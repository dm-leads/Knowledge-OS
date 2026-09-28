"""Подкоманда `hypothesis` (задача 7б.2): завести, показать по фильтру, перевести статус.

Числа гипотеза берёт из хранилища по их номерам, а не из ключа команды: готового разбора числа из JSON в ядре нет, и
ручной дал бы агенту писать любое значение с любым статусом мимо стражей 1 и 8. Поэтому `--json` несёт тексты, номера
уже записанных чисел и координаты единицы действия; числа кладёт в хранилище движок экономики (`economy_run`,
`numbers_sheet`).

Переходы статусов проверены тестами ядра (`test_registry.py`, `test_measure_and_state.py`) — здесь проверяется, что
команда зовёт ядро и отчитывается числом строк, а не повторяет его правила.
"""
import json
import re
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from growth_engine.core.artifacts import Branch, GoalTree
from growth_engine.core.ladders import UnitOfAction
from growth_engine.core.number import Status
from growth_engine.core.storage import RegistryStore, number_id
from growth_engine.registry import run
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.helpers import CARD, CFG, EFFECT, EFFECT_RUB, FACT, FORMULA, UNIT, n
from growth_engine.tests.test_artifacts import CLASS_STATUS, route

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = str(FIXTURES / "config_minimal.yaml")
TODAY = date(2026, 9, 15)


def store(folder) -> RegistryStore:
    return RegistryStore(MarkdownBackend(Path(folder)), allowed_domains=(), today=lambda: TODAY)


def tree_with_unit(unit):
    """Дерево цели, где ветка карточки несёт принятую шагом 3 единицу действия (или не несёт вовсе)."""
    goal = replace(n("sales_entry", 1315, status=Status.ESTIMATE, source="C:модель:цель окна"), flow="all")
    ceiling = replace(n("sales_entry", 400, status=Status.ESTIMATE, source="C:модель:потолок ветки"), flow="all")
    return GoalTree(goal=goal, branches=(Branch(CARD["tree_branch"], "веб-поток", ceiling, unit_of_action=unit),))


def with_numbers(folder):
    """Хранилище, в котором числа гипотезы уже записаны: так их и кладёт движок экономики."""
    s = store(folder)
    s.create_numbers([FACT, EFFECT, EFFECT_RUB])
    return s


def card_json(**changes) -> str:
    """Карточка кандидата: тексты, номера уже записанных чисел и координаты единицы действия.

    `changes` заменяет любое поле карточки — в том числе номер факта-основания, когда тест проверяет чужой класс.
    """
    card = {
        "formulation": FORMULA,
        "cycle_id": CARD["cycle_id"], "business_task": CARD["business_task"], "tree_branch": CARD["tree_branch"],
        "model_lever": CARD["model_lever"], "mechanic": CARD["mechanic"], "main_metric": CARD["main_metric"],
        "owner": CARD["owner"],
        "unit_of_action": {"source_class": UNIT.source_class, "coordinates": dict(UNIT.coordinates)},
        "fact_basis_id": number_id(FACT),
        "effect_goal_units_id": number_id(EFFECT),
        "effect_rub_id": number_id(EFFECT_RUB),
    }
    card.update(changes)
    return json.dumps(card, ensure_ascii=False)


def call(folder, **changes):
    args = Namespace(cmd="hypothesis", config=CONFIG, out=str(folder), lark_book=None, dry_run=False,
                     cycle="Ц-1", id=None, status=None, json=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    return run(args, out=lines.append, today=lambda: TODAY), lines


# --- заведение ---

def test_idea_needs_only_formulation(tmp_path):
    """Гипотеза заводится в статусе «идея»: для неё обязательна только формулировка."""
    code, lines = call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-100")
    assert stored.status.value == "идея" and stored.formulation == FORMULA


def test_idea_keeps_card_fields_given_with_it(tmp_path):
    """До 1.1.0 идея молча теряла всё, кроме формулировки; теперь поданные поля карточки хранятся."""
    code, lines = call(tmp_path, id="H-100", json=json.dumps(
        {"formulation": FORMULA, "cycle_id": "Ц-2", "business_task": "объём лидов", "owner": "Дмитрий"},
        ensure_ascii=False))
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-100")
    assert (stored.cycle_id, stored.business_task, stored.owner) == ("Ц-2", "объём лидов", "Дмитрий")


def test_idea_with_a_field_outside_the_card_is_guard_9(tmp_path):
    code, lines = call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA, "threshold": 0.1},
                                                             ensure_ascii=False))
    assert code == 1 and "[страж 9]" in lines[-1] and "threshold" in lines[-1]


def test_new_version_of_an_idea_archives_the_old_one(tmp_path):
    """Новая формулировка до запуска: номер H-100.v2, прежняя — в архиве с причиной, текст сохранён."""
    call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA, "cycle_id": "Ц-2"}, ensure_ascii=False))
    changed = FORMULA.replace("CR1 страницы", "CR1 формы")
    code, lines = call(tmp_path, id="H-100.v2", json=json.dumps({"formulation": changed, "supersedes": "H-100"},
                                                                ensure_ascii=False))
    assert code == 0, lines[-1]
    new, = store(tmp_path).read("hypotheses", id="H-100.v2")
    old, = store(tmp_path).read("hypotheses", id="H-100")
    assert (new.version, new.supersedes, new.cycle_id, new.status.value) == (2, "H-100", "Ц-2", "идея")
    assert old.status.value == "архив" and "H-100.v2" in old.status_reason and old.formulation == FORMULA


def test_new_version_needs_the_next_number(tmp_path):
    call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    code, lines = call(tmp_path, id="H-101", json=json.dumps({"formulation": FORMULA, "supersedes": "H-100"},
                                                             ensure_ascii=False))
    assert code == 1 and "H-100.v2" in lines[-1]


def test_new_version_of_a_launched_hypothesis_goes_through_close(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, id="H-001", status="candidate", json=card_json())
    code, lines = call(tmp_path, id="H-001.v2", json=json.dumps({"formulation": FORMULA, "supersedes": "H-001"},
                                                                ensure_ascii=False))
    assert code == 1 and "[страж 7]" in lines[-1] and "iterate" in lines[-1]


def test_new_version_of_absent_hypothesis_is_guard_13(tmp_path):
    code, lines = call(tmp_path, id="H-404.v2", json=json.dumps({"formulation": FORMULA, "supersedes": "H-404"},
                                                                ensure_ascii=False))
    assert code == 1 and "[страж 13]" in lines[-1]


def test_created_hypothesis_is_reported_by_rows(tmp_path):
    """Успех — число записанных и прочитанных строк по каждому листу, а не слово «готово»."""
    _, lines = call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    report = [line for line in lines if "записано" in line]
    assert report and all(re.search(r"записано (\d+), прочитано \1$", line) for line in report), report


def test_card_numbers_come_from_storage_by_id(tmp_path):
    """Числа карточки — номера уже записанных чисел: команда их не сочиняет."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, id="H-001", status="candidate", json=card_json())
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-001")
    assert stored.status.value == "candidate" and stored.fact_basis == FACT and stored.effect_rub == EFFECT_RUB
    assert stored.unit_of_action == UNIT


def test_unknown_number_id_is_guard_13(tmp_path):
    """Ссылка на число, которого нет на листе снимков, — страж 13, а не молчаливый пропуск."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, id="H-001", status="candidate", json=card_json(fact_basis_id="0" * 16))
    assert code == 1 and "[страж 13]" in lines[-1] and "0000000000000000" in lines[-1]


def test_candidate_without_card_fields_is_guard_6(tmp_path):
    """Кандидат без обязательных полей карточки не заводится: правило держит ядро, команда его не обходит."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, id="H-001", status="candidate",
                       json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    assert code == 1 and "[страж 6]" in lines[-1]


def test_candidate_needs_its_class_on_step_six_of_the_route(tmp_path):
    """Дефект приёмочного прогона 7б: цепочка шагов не была защищена.

    В прогоне гипотезу завели при непройденном шаге 3, и ничто этому не помешало. Ядро умеет проверять связь
    («класс факта-основания обязан быть перечислен на шаге 6 маршрута цикла»), но команда её не звала. Если маршрут
    цикла лежит в хранилище, проверка обязана сработать.

    Красный случай — факт-основание класса B: маршрут фикстуры законен и несёт на шаге 6 классы C, D, F. Подменять
    сам маршрут нельзя: шаг 6 обязан нести класс главной метрики, и такой маршрут не записался бы вовсе (страж 9).
    """
    s = with_numbers(tmp_path)
    foreign = replace(FACT, source="B:реклама-x:кампании")
    s.create_numbers([foreign])
    s.create(replace(route(), cycle_id=CARD["cycle_id"]), class_status=CLASS_STATUS)
    code, lines = call(tmp_path, id="H-001", status="candidate",
                       json=card_json(fact_basis_id=number_id(foreign)))
    assert code == 1, "гипотеза заведена, хотя класс её факта-основания не перечислен на шаге 6 маршрута"
    # Проверяется дело, а не падеж: отказ называет номер шага и класс. Первая версия требовала подстроку «шаг 6»,
    # а движок пишет «на шаге 6» — тест падал бы на форме осмысленного текста.
    assert "6" in lines[-1] and "B" in lines[-1] and "маршрут" in lines[-1], lines[-1]


def test_candidate_passes_when_the_route_lists_its_class(tmp_path):
    """Тот же маршрут, где класс факта-основания перечислен: заведение проходит."""
    s = with_numbers(tmp_path)
    s.create(replace(route(), cycle_id=CARD["cycle_id"]), class_status=CLASS_STATUS)
    code, lines = call(tmp_path, id="H-001", status="candidate", json=card_json())
    assert code == 0, lines[-1]


def test_candidate_must_stand_on_the_unit_of_action_of_its_branch(tmp_path):
    """Дыра ворот 7б: единица действия гипотезы ни с чем не сверялась.

    Шаг 3 принимает узкое место на единице действия и записывает её в ветку дерева цели. Гипотеза шага 6 обязана
    стоять на той же единице: иначе диагноз доводится до конкретной кнопки, а гипотеза пишется про что угодно.
    """
    s = with_numbers(tmp_path)
    s.create(tree_with_unit(UnitOfAction("A", {"url": "/other/", "element": "другая форма"})))
    code, lines = call(tmp_path, id="H-001", status="candidate", json=card_json())
    assert code == 1, "гипотеза заведена на единице действия, которой нет в её ветке"
    assert "единиц" in lines[-1].lower() and CARD["tree_branch"] in lines[-1], lines[-1]


def test_candidate_passes_when_the_unit_matches_the_branch(tmp_path):
    """Та же единица действия, что принята шагом 3, — заведение проходит."""
    s = with_numbers(tmp_path)
    s.create(tree_with_unit(UNIT))
    code, lines = call(tmp_path, id="H-001", status="candidate", json=card_json())
    assert code == 0, lines[-1]


def test_branch_without_unit_of_action_does_not_block(tmp_path):
    """Ветка без принятой единицы — шаг 3 по ней ещё не проходил: команда не требует того, чего нет."""
    s = with_numbers(tmp_path)
    s.create(tree_with_unit(None))
    code, lines = call(tmp_path, id="H-001", status="candidate", json=card_json())
    assert code == 0, lines[-1]


def test_candidate_without_a_route_is_allowed(tmp_path):
    """Маршрута в хранилище нет — проверять нечего: команда не требует того, чего владелец ещё не согласовал."""
    with_numbers(tmp_path)
    code, lines = call(tmp_path, id="H-001", status="candidate", json=card_json())
    assert code == 0, lines[-1]


# --- показ по фильтру ---

def test_listing_filters_by_status(tmp_path):
    """`--status` — фильтр показа, когда нет полей записи: строка ключа переводится в статус контракта."""
    with_numbers(tmp_path)
    call(tmp_path, id="H-001", status="candidate", json=card_json())
    call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    code, lines = call(tmp_path, status="candidate")
    assert code == 0
    assert any("H-001" in line for line in lines) and not any("H-100" in line for line in lines)


def test_listing_without_filter_shows_every_hypothesis(tmp_path):
    with_numbers(tmp_path)
    call(tmp_path, id="H-001", status="candidate", json=card_json())
    call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    code, lines = call(tmp_path)
    assert code == 0 and any("H-001" in line for line in lines) and any("H-100" in line for line in lines)


def test_unknown_status_is_code_1(tmp_path):
    code, lines = call(tmp_path, status="почти готово")
    assert code == 1 and "почти готово" in lines[-1]


def test_empty_registry_says_so(tmp_path):
    """Пустой реестр — «ничего не найдено», а не пустой вывод: молчание читается как успех."""
    code, lines = call(tmp_path, status="candidate")
    assert code == 0 and "не найдено" in lines[-1]


# --- перевод статуса ---

def test_transition_goes_through_the_registry(tmp_path):
    """Перевод статуса — через `update_status`, значит стражи переходов действуют и здесь."""
    with_numbers(tmp_path)
    call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    code, lines = call(tmp_path, id="H-100", status="research")
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-100")
    assert stored.status.value == "research"


def test_transition_not_in_contract_is_code_1(tmp_path):
    """«идея → в тесте» контрактом не предусмотрен: команда обязана отказать, а не дописать строку."""
    with_numbers(tmp_path)
    call(tmp_path, id="H-100", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    code, lines = call(tmp_path, id="H-100", status="в тесте")
    assert code == 1 and "переход" in lines[-1]


def test_transition_of_absent_hypothesis_is_code_1(tmp_path):
    with_numbers(tmp_path)
    code, lines = call(tmp_path, id="H-404", status="research")
    assert code == 1 and "H-404" in lines[-1]
