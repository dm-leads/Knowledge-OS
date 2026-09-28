"""ПДн и секреты до записи в хранилище (задача 5.2, страж 12): находка останавливает запись, значение не печатается.

Проверка идёт по строкам листов уже в виде ячеек, поэтому видит текст внутри JSON и ссылок.
"""
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from growth_engine.core.artifacts import Branch, DecisionEntry, GoalTree, KnowledgeEntry, SourceMapEntry
from growth_engine.core.config import parse_config
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.core.router import route_draft
from growth_engine.core.storage import (check_rows_pii, decision_row, find_pii, hypothesis_row, knowledge_row,
                                        number_row, route_rows, source_row, tree_rows)
from growth_engine.tests.helpers import candidate, concluded, n
from growth_engine.tests.test_artifacts import route
from growth_engine.tests.test_router import ALL, FORM
from growth_engine.tests.test_storage_codec import NUMBERS, launched

DOMAINS = ("пример-компании.рф", "api.analytics-x.example")
PHONE = "+7 (999) 123-45-67"


def fixture_rows() -> dict:
    """Строки всех семи листов из фикстур ядра — реалистичные тексты реестра без ПДн."""
    rows = {"numbers": [number_row(x) for x in NUMBERS], "hypotheses": []}
    for hypothesis in (candidate(), launched(), concluded()):
        row, numbers = hypothesis_row(hypothesis)
        rows["hypotheses"].append(row)
        rows["numbers"] += [number_row(x) for x in numbers]
    closed = route_draft("Ц-1", "окно New First SQL", "возвращаемость", FORM, ALL, "C", date(2026, 9, 14), False)
    rows["routes"] = route_rows(closed) + route_rows(replace(route(), cycle_id="Ц-2"))
    tree = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE),
                    branches=(Branch("B1", "веб-поток", n("sales_entry", 400, flow="web"), ("H-001",)),))
    rows["trees"], tree_numbers = tree_rows(tree)
    rows["numbers"] += [number_row(x) for x in tree_numbers]
    rows["knowledge"] = [knowledge_row(KnowledgeEntry(id="K-001", statement="шаг 2 формы подбора не барьер",
                                                      verdict="опровергнуто", on=date(2026, 10, 20),
                                                      source="C:analytics-x:data", hypothesis_id="H-001"))]
    rows["decisions"] = [decision_row(DecisionEntry("Ц-0", "H1 → добыча данных", "починка атрибуции",
                                                    subtraction="гипотеза H1 снята", id="D-v1-H1"))]
    rows["sources"] = [source_row(SourceMapEntry(
        name="аналитика-x", source_class="C", status="подключён", history_from="2021-04",
        truth_point="заявки по каналу; ответ https://api.analytics-x.example/v1/data",
        probe="шаблон https://{ACCOUNT}.crm.example/api/v4 за 1 день", traps=("фильтр режет визиты",),
        secret_env_names=("API_KEY_X",)))]
    return rows


def test_fixture_rows_of_all_seven_sheets_have_no_findings():
    rows = fixture_rows()
    assert set(rows) == {"numbers", "hypotheses", "routes", "trees", "knowledge", "decisions", "sources"}
    for sheet, sheet_rows in rows.items():
        check_rows_pii(sheet, sheet_rows, DOMAINS)


@pytest.mark.parametrize("text, kind", [
    ("перезвонить +7 (999) 123-45-67", "телефон"), ("номер 8 999 123 45 67", "телефон"),
    ("89991234567", "телефон"), ("+79991234567", "телефон"), ("звонок на 89991234567.", "телефон"), ("клиент из Минска +375291234567", "телефон"),
    ("пишите ivanov.petr@mail.example", "адрес почты"),
    ("карточка https://company.crm.example/leads/detail/123", "ссылка на домен вне списка инстанса"),
    ("https://api.analytics-x.example/v1/data?email=x", "строка запроса в ссылке"),
    ("токен y0_AgAAAAB4bWx0eHh4eHh4eHh4eHh4eHh4eHh4eHh4eA", "похоже на секрет"),
    ("ключ sk-live-A1b2C3d4E5f6G7h8I9j0K1l2M3n4", "похоже на секрет"),
    ("id a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8", "похоже на секрет"),
    ('["позвонить +7 999 123-45-67"]', "телефон"),
])
def test_findings(text, kind):
    assert kind in find_pii(text, DOMAINS)


@pytest.mark.parametrize("text", [
    "0b7c3c6e-1f2a-4b8e-9d3a-5a1f2e3d4c5b", "77d78ac95df2ddbe", "2026-09-03", "12654556.0", "13630.82089552241", "0,79991234567", "89991234567.5", "visits=101146",
    "marker_level_1=seo", "API_KEY_X", "35,71%", "https://api.analytics-x.example/v1/data",
    "https://сайт.пример-компании.рф/catalog/item/", "https://{ACCOUNT}.crm.example/api/v4", "payments_with_new_first",
    "8 064 заявки, 1 994 New First SQL", "C:analytics-x:project/analytics/data",
    # 1.2.1: адрес страницы из слов через дефис с годом — не секрет (страж остановил запись страниц рейтингов)
    "landing_page=/blog/rejting-ochistitelej-vozduha-dlya-kvartiry-luchshie-modeli-2025-goda",
    "/blog/luchshie-brizery-dlya-kvartiry-2026-goda-top-10",
])
def test_registry_texts_are_not_findings(text):
    assert find_pii(text, DOMAINS) == []


@pytest.mark.parametrize("sheet", ["numbers", "hypotheses", "routes", "trees", "knowledge", "decisions", "sources"])
def test_phone_in_any_column_of_any_sheet_stops_without_echo(sheet):
    rows = fixture_rows()[sheet]
    for column in rows[0]:
        poisoned = [{**rows[0], column: f"{rows[0][column]} {PHONE}"}]
        with pytest.raises(GuardViolation) as e:
            check_rows_pii(sheet, poisoned, DOMAINS)
        assert e.value.guard == 12 and f"«{column}»" in str(e.value) and "999" not in str(e.value)


def test_link_domains_come_from_instance_configuration():
    raw = yaml.safe_load((Path(__file__).parent / "fixtures" / "config_minimal.yaml").read_text(encoding="utf-8"))
    assert parse_config(raw).storage_link_domains == ()
    raw["storage"]["allowed_link_domains"] = ["Сайт.РФ", "api.analytics-x.example"]
    assert parse_config(raw).storage_link_domains == ("сайт.рф", "api.analytics-x.example")
    for bad in (["https://сайт.рф"], ["сайт.рф/catalog"], [""], "сайт.рф"):
        raw["storage"]["allowed_link_domains"] = bad
        with pytest.raises(ValueError):
            parse_config(raw)
