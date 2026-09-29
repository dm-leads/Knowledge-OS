"""MQL — промежуточная ступень воронки «обращение → MQL → первый квалифицированный» (миграция 0014, версия 1.1.0).

Правило — параметр конфигурации: воронки квалификации, статус «квалифицирован» с целевым маршрутом (новое или прежнее
поле маршрута), статус «закрыт» с настоящей причиной отказа. У первого инстанса ядро повторило MQL внешней системы
сквозной аналитики на трёх окнах в двух кабинетах: пять совпали точно, одно — на единицу."""
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from datacore.schema.config import load_config
from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import migrate
from datacore.schema.number import Status
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import mql
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
SEP = (date(2026, 9, 1), date(2026, 10, 1))
NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def deal(deal_id, *, pipeline="300", status="142", route=None, legacy=None, reason=None, brand="b1", day=5):
    return dict(deal_id=deal_id, contact_id=1, brand=brand, pipeline=pipeline, status=status,
                created_at=datetime(2026, 9, day, 10, tzinfo=timezone.utc), is_new_first=False,
                qualification_route=route, qualification_route_legacy=legacy, close_reason=reason)


def load(engine, rows):
    migrate(engine)
    p = Provenance("crm_mirror", "D", "L-crm", NOW)
    register_load(engine, p, NOW.date())
    write_facts(engine, "contact", [dict(contact_id=1, brand="b1", created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))],
                p, mode="upsert")
    write_facts(engine, "deal", list(rows), p, mode="upsert")
    finish_load(engine, p.load_id, rows_read=len(rows), rows_written=len(rows), status="ok")


def test_three_branches_count_and_the_rest_do_not(engine):
    """Квалифицирован с маршрутом в продажи (новое поле, в том числе среди нескольких значений; или прежнее поле) и
    закрыт по настоящей причине — MQL. Маршрут не в продажи, причина не из списка, закрытие без причины — нет."""
    load(engine, [
        deal(1, route="Маршрут А"),                               # ветка: новое поле
        deal(2, route="Маршрут А; Маршрут Б"),                       # несколько значений маршрута
        deal(3, route="Маршрут Б", legacy="Маршрут А"),              # ветка: прежнее поле
        deal(4, status="143", route="Маршрут А", reason="Причина 1"),   # настоящий отказ
        deal(5, route="Маршрут Б"),                                     # не в продажи
        deal(6, status="143", route="Маршрут А", reason="Причина 9"),
        deal(7, status="143", route="Маршрут А"),                 # закрыт без причины
        deal(8, status="999", route="Маршрут А"),                 # ещё в работе у квалификатора
        deal(9, pipeline="500", route="Маршрут А"),               # не воронка квалификации
    ])
    n = mql(engine, CFG, "company", *SEP)
    assert n.value == 4 and n.status is Status.FACT, n.missing
    assert "передано в продажи: 3; закрыто по настоящей причине: 1" in n.missing


def test_brand_by_field_and_company_by_all_facts(engine):
    """Бренд — поле сделки, не воронка: лид второго бренда в общей воронке квалификации считается у второго бренда."""
    load(engine, [deal(1, route="Маршрут А"), deal(2, route="Маршрут А", brand="b2"),
                  deal(3, pipeline="301", route="Маршрут А", brand="b2")])
    assert mql(engine, CFG, "b1", *SEP).value == 1
    assert mql(engine, CFG, "b2", *SEP).value == 2
    assert mql(engine, CFG, "company", *SEP).value == 3


def test_pipeline_segment_gives_the_external_project_view(engine):
    """Разрез по воронке: внешняя система, у которой проект равен воронке, сверяется с ядром один к одному."""
    load(engine, [deal(1, route="Маршрут А"), deal(2, route="Маршрут А", brand="b2"),
                  deal(3, pipeline="301", route="Маршрут А", brand="b2")])
    first = mql(engine, CFG, "company", *SEP, segment="pipeline=300")
    assert first.value == 2 and first.segment == "pipeline=300"
    assert mql(engine, CFG, "company", *SEP, segment="pipeline=301").value == 1
    with pytest.raises(RuleViolation) as e:
        mql(engine, CFG, "company", *SEP, segment="pipeline=500")           # не воронка квалификации
    assert e.value.code == "К2"


def test_unloaded_qualification_fields_are_no_data_not_zero(engine):
    """Сделки в воронках квалификации есть, а полей квалификации нет ни у одной — они не загружены. Ноль здесь был бы
    ложью: «никого не квалифицировали» вместо «не знаем»."""
    load(engine, [deal(1), deal(2, status="143")])
    n = mql(engine, CFG, "company", *SEP)
    assert n.value is None and n.status is Status.NO_DATA and "не загружены" in n.missing


def test_unknown_brand_lowers_status_and_is_named(engine):
    """Сделка MQL без бренда: у компании входит в число, у кабинета — неопределённость. Не-MQL без бренда статус не
    трогает (считается тем же отбором, что и число), и в виде проекта бренд не участвует."""
    load(engine, [deal(1, route="Маршрут А"), deal(2, route="Маршрут А", brand="unknown"),
                  deal(3, route="Маршрут Б", brand="unknown")])
    n = mql(engine, CFG, "company", *SEP)
    assert n.value == 2 and n.status is Status.ESTIMATE and "MQL с неопределённым брендом в окне: 1" in n.missing
    assert mql(engine, CFG, "b1", *SEP).status is Status.ESTIMATE
    assert mql(engine, CFG, "company", *SEP, segment="pipeline=300").status is Status.FACT


def test_fresh_window_with_deals_in_progress_is_zero_not_no_data(engine):
    """Все сделки окна ещё у квалификатора — полей у них и не должно быть: MQL = 0 как факт, а не «не загружены»."""
    load(engine, [deal(1, status="999"), deal(2, status="998")])
    n = mql(engine, CFG, "company", *SEP)
    assert n.value == 0 and n.status is Status.FACT, n.missing


def test_partly_loaded_fields_lower_status_and_are_counted(engine):
    """История не перечитана из источника: у части закрытых сделок полей нет — число занижено, это оценка."""
    load(engine, [deal(1, route="Маршрут А"), deal(2), deal(3, status="143")])
    n = mql(engine, CFG, "company", *SEP)
    assert n.value == 1 and n.status is Status.ESTIMATE
    assert "у 2 из 3 закрытых квалификатором сделок нет полей квалификации" in n.missing


def test_rule_is_required_and_segment_is_checked(engine):
    load(engine, [deal(1, route="Маршрут А")])
    with pytest.raises(RuleViolation) as e:
        mql(engine, replace(CFG, mql=None), "company", *SEP)
    assert e.value.code == "К2" and "правила MQL" in str(e.value)
    with pytest.raises(RuleViolation) as e:
        mql(engine, CFG, "company", *SEP, segment="contour=full")
    assert e.value.code == "К2"
