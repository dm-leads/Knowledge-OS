"""Строки семи артефактов хранилища (задача 5.1): номер числа, кодирование ячеек, чтение и запись без потерь.

Правило ячеек — из пробы таблицы 15.09.2026: строка с «=» становится формулой, true/false — логическим, «#REF!» —
ошибкой; ведущий апостроф сохраняется символом, поэтому экранирование им обратимо.
"""
import unicodedata
from dataclasses import fields, replace
from datetime import date

import pytest

from growth_engine.core.artifacts import Branch, DecisionEntry, GoalTree, KnowledgeEntry, SourceMapEntry
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.core.registry import HStatus, Hypothesis, create, transition
from growth_engine.core.router import route_draft
from growth_engine.core.storage import (COLUMNS, HYPOTHESIS_KINDS, NUMBER_IDENTITY, SHEETS, decision_from_row,
                                        decision_row, decode_text, encode_text, hypothesis_from_row, hypothesis_row,
                                        knowledge_from_row, knowledge_row, number_from_row, number_id, number_row,
                                        numbers_by_id, route_rows, routes_from_rows, source_from_row, source_row,
                                        tree_rows, trees_from_rows)
from growth_engine.tests.helpers import FORMULA, LAUNCH, candidate, concluded, gated, measured, n
from growth_engine.tests.test_artifacts import route
from growth_engine.tests.test_router import ALL, FORM

BASE = replace(n("leads", 1981, flow="web"), segment="marker_level_1=seo")


def launched():
    return transition(gated(), HStatus.IN_TEST, **LAUNCH)


def test_seven_artifacts_have_sheets():
    assert set(SHEETS.values()) == {"Карта источников", "Модель — снимки", "Дерево цели", "Маршрут цикла",
                                    "Гипотезы", "Карта знаний", "Журнал решений"}
    assert set(COLUMNS) == set(SHEETS)


# --- номер числа ---

IDENTITY_CHANGES = [("metric", "visits"), ("level", "visit"), ("scope", "p2"), ("flow", "all"),
                    ("segment", "marker_level_1=direct"), ("period_start", date(2026, 7, 1)),
                    ("period_end", date(2026, 9, 2)), ("as_of", date(2026, 9, 14)), ("source", "C:analytics-x:other"),
                    ("denominator", "visits=100"), ("unit", "₽")]


def test_identity_changes_cover_every_identity_field():
    assert [name for name, _ in IDENTITY_CHANGES] == list(NUMBER_IDENTITY)


@pytest.mark.parametrize("field, other", IDENTITY_CHANGES)
def test_number_id_changes_with_each_identity_field(field, other):
    assert number_id(replace(BASE, **{field: other})) != number_id(BASE)


def test_number_id_ignores_value_status_and_note():
    same = replace(BASE, value=2000.0, status=Status.ESTIMATE, missing="оговорка")
    assert number_id(same) == number_id(BASE) and len(number_id(BASE)) == 16


def test_number_id_normalizes_unicode():
    composed = replace(BASE, metric=unicodedata.normalize("NFC", "заявки й"))
    decomposed = replace(BASE, metric=unicodedata.normalize("NFD", "заявки й"))
    assert number_id(composed) == number_id(decomposed)


# К1: порог шума гейта (на базе доли) и порог при запуске (на ожидаемом N теста) — разные числа, не конфликт.
def test_gate_and_launch_noise_thresholds_get_different_ids():
    assert number_id(gated().noise_threshold) != number_id(launched().noise_threshold)


# --- ячейки ---

@pytest.mark.parametrize("text", ["=1+1", "'кавычка", "''двойной", "#REF!", "#хэштег", "True", "false", " TRUE "])
def test_text_the_sheet_would_convert_is_escaped_and_restored(text):
    cell = encode_text(text)
    assert cell.startswith("'") and decode_text(cell) == text


@pytest.mark.parametrize("text", ["+7 999", "@home", "-0.05", "да", "2026-09-03", "0.0156", "", "первая\nвторая"])
def test_plain_text_is_written_as_is(text):
    assert encode_text(text) == text and decode_text(text) == text


@pytest.mark.parametrize("space", [" ", " ", " ", "⁠"])
def test_invisible_spaces_are_normalised_before_writing(space):
    """Живой Lark молча заменяет неразрывный пробел обычным — номер числа перестаёт сходиться с содержимым.

    Найдено первым живым циклом 17.09.2026: названия каналов Roistat несут U+00A0 («Прямые визиты», «Визиты с
    сайтов»), и после записи строка читалась как изменённая вне движка (страж 13). Поддельный мост такого искажения
    не делает, поэтому тесты его не ловили. Лечение — в кодировщике: движок сам приводит невидимые пробелы к
    обычным, и записанное совпадает с прочитанным.
    """
    assert encode_text(f"channel=Прямые{space}визиты") == "channel=Прямые визиты"


def test_number_id_survives_a_round_trip_with_nbsp():
    """Номер числа с неразрывным пробелом в сегменте сходится после записи и чтения обратно."""
    number = replace(n("leads", 573), segment="channel=Прямые визиты")
    restored = number_from_row(number_row(number))
    assert restored.segment == "channel=Прямые визиты"


@pytest.mark.parametrize("cell", [0.0156, True, None])
def test_non_text_cell_is_rejected(cell):
    with pytest.raises(GuardViolation) as e:
        decode_text(cell)
    assert e.value.guard == 13


# --- числа ---

NUMBERS = [n("sales_entry", 587), n("leads", None, "p2", status=Status.NO_DATA), BASE,
           replace(n("gross_profit", 12654556.0), unit="₽", status=Status.PROXY,
                   missing="=деньги | зависят от атрибуции\nвторая строка"),
           replace(n("leads", 0.0156), unit="доля", denominator="visits=101146")]


def test_number_rows_round_trip():
    rows = [number_row(x) for x in NUMBERS]
    assert all(set(row) == set(COLUMNS["numbers"]) for row in rows)
    assert [number_from_row(row) for row in rows] == NUMBERS


def test_number_row_edited_outside_engine_stops():
    row = {**number_row(NUMBERS[0]), "metric": "leads"}
    with pytest.raises(GuardViolation, match="не совпадает") as e:
        number_from_row(row)
    assert e.value.guard == 13


def test_same_number_id_with_other_value_stops():
    rows = [number_row(NUMBERS[0]), number_row(replace(NUMBERS[0], value=600))]
    with pytest.raises(GuardViolation) as e:
        numbers_by_id(rows)
    assert e.value.guard == 13


def test_missing_column_stops():
    row = number_row(NUMBERS[0])
    del row["unit"]
    with pytest.raises(GuardViolation, match="unit") as e:
        number_from_row(row)
    assert e.value.guard == 13


# --- гипотезы ---

@pytest.mark.parametrize("make", [lambda: create(id="H-000", formulation=FORMULA), candidate, gated, launched,
                                  measured, concluded],
                         ids=["идея", "candidate", "после гейта", "в тесте", "замер", "вывод"])
def test_hypothesis_round_trip_in_every_status(make):
    hypothesis = make()
    row, numbers = hypothesis_row(hypothesis)
    assert set(row) == set(COLUMNS["hypotheses"])
    assert hypothesis_from_row(row, numbers_by_id(number_row(x) for x in numbers)) == hypothesis


def test_hypothesis_with_missing_number_stops():
    row, _ = hypothesis_row(candidate())
    with pytest.raises(GuardViolation, match="Модель — снимки") as e:
        hypothesis_from_row(row, {})
    assert e.value.guard == 13


# Х11: новое нетекстовое поле гипотезы не должно молча записаться текстом.
def test_every_non_text_hypothesis_field_has_a_kind():
    assert [f.name for f in fields(Hypothesis) if f.type != "str" and f.name not in HYPOTHESIS_KINDS] == []


# --- маршрут и дерево цели ---

def test_route_rows_round_trip():
    closed = route_draft("Ц-1", "окно New First SQL", "возвращаемость", FORM, ALL, "C", date(2026, 9, 14), False)
    plain = replace(route(), cycle_id="Ц-2")
    rows = route_rows(closed) + route_rows(plain)
    assert all(set(row) == set(COLUMNS["routes"]) for row in rows)
    assert routes_from_rows(rows) == [closed, plain]


def test_route_rows_disagreeing_on_route_fields_stop():
    rows = route_rows(route())
    rows[3] = {**rows[3], "goal": "другая цель"}
    with pytest.raises(GuardViolation) as e:
        routes_from_rows(rows)
    assert e.value.guard == 13


# Известный ответ дерева цели из тестов ядра: цель 1 315, ветки 400 и 300.
def test_goal_tree_round_trip():
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE),
                    branches=(Branch("B1", "веб-поток", n("sales_entry", 400, flow="web", status=Status.ESTIMATE),
                                     ("H-001",)),
                              Branch("B2", "без визита", n("sales_entry", 300, flow="no_visit"))))
    rows, numbers = tree_rows(tree)
    assert all(set(row) == set(COLUMNS["trees"]) for row in rows)
    assert trees_from_rows(rows, numbers_by_id(number_row(x) for x in numbers)) == [tree]


# --- карта знаний, журнал решений, карта источников ---

def test_knowledge_decision_and_source_rows_round_trip():
    knowledge = KnowledgeEntry(id="K-001", statement="шаг 2 формы подбора не барьер", verdict="опровергнуто",
                               on=date(2026, 10, 20), source="C:analytics-x:data", hypothesis_id="H-001")
    decision = DecisionEntry("Ц-0", "H1 → добыча данных", "починка атрибуции, а не рычаг роста",
                             subtraction="гипотеза H1 снята из реестра", alternatives=("оставить гипотезой",),
                             id="D-v1-H1")
    source = SourceMapEntry(name="аналитика-x", source_class="C", status="подключён", history_from="2021-04",
                            truth_point="заявки", probe="analytics/data за 1 день",
                            traps=("фильтр режет визиты", "=формула в ловушке"), secret_env_names=("API_KEY_X",))
    assert set(knowledge_row(knowledge)) == set(COLUMNS["knowledge"])
    assert knowledge_from_row(knowledge_row(knowledge)) == knowledge
    assert decision_from_row(decision_row(decision)) == decision
    assert source_from_row(source_row(source)) == source


def test_decision_without_id_cannot_be_stored():
    with pytest.raises(GuardViolation) as e:
        decision_row(DecisionEntry("Ц-1", "kill H-001", "эффект ниже порога", subtraction="сняли шаг 2"))
    assert e.value.guard == 13
