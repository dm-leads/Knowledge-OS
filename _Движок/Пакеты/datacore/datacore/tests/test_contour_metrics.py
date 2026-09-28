"""Одна модель с разными наборами отбора даёт разные числа (требование Я12): все сделки, первые
квалифицированные, они же с исключениями контура. У первого инстанса числа проверены на живых фактах
16–17.09.2026 до написания плана."""
from datetime import date, datetime, timezone

import pytest

from datacore.schema.config import load_config
from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import new_first_sql
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
AUG = (date(2026, 8, 1), date(2026, 9, 1))
NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def deal(deal_id, *, tech=None, summary=None, new_first=True, brand="b1"):
    return dict(deal_id=deal_id, contact_id=1, brand=brand, pipeline="100", status="142",
                created_at=datetime(2026, 8, 5, tzinfo=timezone.utc), is_new_first=new_first,
                entry_channel_tech=tech, entry_channel_summary=summary)


def marker(deal_id, level_1, system="tracker_b1"):
    return dict(deal_id=deal_id, marker=f"{level_1}_x", visit_id=None,
                ordered_at=datetime(2026, 8, 5, tzinfo=timezone.utc), marker_level_1=level_1), system


def setup_deals(engine, rows, markers):
    """Сделки CRM и маркеры заказов: маркеры раскладываются по своим кабинетам."""
    migrate(engine)
    p = Provenance("crm_mirror", "D", "L-crm", NOW)
    register_load(engine, p, NOW.date())
    write_facts(engine, "contact", [dict(contact_id=1, brand="b1",
                                         created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))], p, mode="upsert")
    write_facts(engine, "deal", list(rows), p, mode="upsert")
    finish_load(engine, p.load_id, rows_read=len(rows), rows_written=len(rows), status="ok")
    by_system: dict[str, list] = {}
    for row, system in markers:
        by_system.setdefault(system, []).append(row)
    for system, batch in by_system.items():
        t = Provenance(system, "C", f"L-{system}", NOW)
        register_load(engine, t, NOW.date())
        write_facts(engine, "order_marker", batch, t, mode="upsert")
        finish_load(engine, t.load_id, rows_read=len(batch), rows_written=len(batch), status="ok")


def test_one_model_gives_different_numbers_by_contour(engine):
    """Пять сделок: повторная, обычная, из источника витрины, с техканалом партнёра и с «Рекомендацией»."""
    setup_deals(engine,
                [deal(1), deal(2), deal(3, tech="Лиды партнёра", summary="партнёр"),
                 deal(4, summary="Рекомендация (сосед, знакомый)"), deal(5, new_first=False)],
                [marker(1, "seo"), marker(2, "site"), marker(3, "seo"), marker(4, "seo"), marker(5, "seo")])
    # вопрос собственника: все сделки компании, включая повторные
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="all_deals").value == 5
    assert new_first_sql(engine, CFG, "b1", *AUG).value == 4                         # full — умолчание
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="narrow").value == 3      # без источника site
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="narrow_tech").value == 2  # без «Лиды партнёра»
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="strict").value == 1      # без «Рекомендация»


def test_all_deals_names_its_base_in_the_number(engine):
    """Набор собственника объявляет себя в пояснении: «все сделки» и «первые квалифицированные» нельзя спутать молча."""
    setup_deals(engine, [deal(1), deal(2, new_first=False)], [marker(1, "seo"), marker(2, "seo")])
    n = new_first_sql(engine, CFG, "b1", *AUG, contour="all_deals")
    assert n.value == 2 and n.segment == "contour=all_deals"
    assert "не только первые" in n.missing.lower()


def test_contour_is_named_in_the_number(engine):
    """Число с отбором несёт имя набора: иначе 591 и 630 в отчёте не различить."""
    setup_deals(engine, [deal(1)], [marker(1, "seo")])
    plain = new_first_sql(engine, CFG, "b1", *AUG)
    filtered = new_first_sql(engine, CFG, "b1", *AUG, contour="strict")
    assert plain.segment == ""
    assert filtered.segment == "contour=strict"


def test_marker_of_own_cabinet_only(engine):
    """У сделки два маркера — по одному из каждого кабинета (проверено на фактах: маркеров ровно вдвое больше сделок).
    Берётся маркер своего бренда, иначе сделка посчитается дважды."""
    setup_deals(engine, [deal(1)], [marker(1, "seo"), marker(1, "site", system="tracker_b2")])
    assert new_first_sql(engine, CFG, "b1", *AUG).value == 1
    # маркер чужого кабинета не влияет на отбор: у своего он «seo», значит витрина сделку оставляет
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="narrow").value == 1


def test_unknown_contour_is_refused(engine):
    setup_deals(engine, [deal(1)], [marker(1, "seo")])
    with pytest.raises(RuleViolation) as e:
        new_first_sql(engine, CFG, "b1", *AUG, contour="выдуманный")
    assert e.value.code == "К2"


def test_deal_without_marker_is_counted(engine):
    """Сделка без маркера заказа — всё равно сделка: связь с визитом не условие её существования."""
    setup_deals(engine, [deal(1), deal(2)], [marker(1, "seo")])
    assert new_first_sql(engine, CFG, "b1", *AUG).value == 2


def test_company_counts_deals_without_brand(engine):
    """«Компания» — это все её сделки, а не сумма кабинетов.

    У части сделок бренд не определён: складывая только первый и второй кабинеты, ядро их теряло.
    Та же ошибка была в выручке этапа 4 и исправлена там же — здесь она повторилась в другой метрике."""
    setup_deals(engine,
                [deal(1), deal(2, brand="b2"), deal(3, brand="unknown")],
                [marker(1, "seo"), marker(2, "seo", system="tracker_b2"), marker(3, "seo")])
    n = new_first_sql(engine, CFG, "company", *AUG)
    assert n.value == 3, n.missing
    assert "неопределённым брендом" in n.missing     # бренд без кабинета назван, а не спрятан


def test_contour_can_come_as_segment(engine):
    """Известный ответ задаёт набор разрезом «contour=имя»: проверка известных ответов передаёт метрике segment,
    а не contour. Без этого три ответа про фильтры (591 / 570 / 546) сверялись с числом без отбора и падали."""
    setup_deals(engine, [deal(1), deal(2)], [marker(1, "seo"), marker(2, "site")])
    by_segment = new_first_sql(engine, CFG, "b1", *AUG, segment="contour=narrow")
    by_kwarg = new_first_sql(engine, CFG, "b1", *AUG, contour="narrow")
    assert by_segment.value == by_kwarg.value == 1
    assert by_segment.segment == "contour=narrow"


def test_foreign_segment_is_refused(engine):
    """Разрез не из наборов — отказ К2, а не тихий подсчёт без отбора."""
    setup_deals(engine, [deal(1)], [marker(1, "seo")])
    with pytest.raises(RuleViolation) as e:
        new_first_sql(engine, CFG, "b1", *AUG, segment="channel_group=seo")
    assert e.value.code == "К2"


def test_brand_cannot_be_null_in_facts(engine):
    """Ревью Codex этапа 5 (п.6) предполагал бренд в NULL. Схема этого не допускает: колонка объявлена
    обязательной, и запись такой сделки отвергается правилом К1.

    Неизвестный бренд хранится строкой-заглушкой из конфигурации («unknown») — она и
    понижает статус числа. Защита сортировки пояснения от None оставлена как дешёвая страховка."""
    from datacore.schema.errors import RuleViolation
    migrate(engine)
    p = Provenance("crm_mirror", "D", "L-null", NOW)
    register_load(engine, p, NOW.date())
    write_facts(engine, "contact", [dict(contact_id=1, brand="b1",
                                         created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))], p, mode="upsert")
    with pytest.raises(RuleViolation) as e:
        write_facts(engine, "deal", [dict(deal(1), brand=None)], p, mode="upsert")
    assert e.value.code == "К1"


def test_unknown_brand_lowers_status_and_is_named(engine):
    """Сделка с брендом-заглушкой входит в компанию, но статус становится оценкой: по кабинетам её не разложить."""
    from datacore.schema.number import Status
    setup_deals(engine, [deal(1), deal(2, brand="unknown")], [marker(1, "seo"), marker(2, "seo")])
    n = new_first_sql(engine, CFG, "company", *AUG)
    assert n.value == 2 and n.status is Status.ESTIMATE
    assert "неопределённым брендом" in n.missing, n.missing
