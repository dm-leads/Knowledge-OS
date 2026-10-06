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


# --- «Одна сделка на контакт»: у контакта засчитывается самая ранняя из первых квалифицированных ---

JUL = (date(2026, 7, 1), date(2026, 8, 1))


def contact_deal(deal_id, contact_id, day, *, month=8, new_first=True, deleted_at=None):
    return dict(deal_id=deal_id, contact_id=contact_id, brand="b1", pipeline="100", status="142",
                created_at=datetime(2026, month, day, tzinfo=timezone.utc), is_new_first=new_first,
                entry_channel_tech=None, entry_channel_summary=None, deleted_at=deleted_at)


def setup_contacts(engine, rows, excluded=()):
    migrate(engine)
    p = Provenance("crm_mirror", "D", "L-crm", NOW)
    register_load(engine, p, NOW.date())
    contacts = sorted({r["contact_id"] for r in rows})
    write_facts(engine, "contact", [dict(contact_id=c, brand="b1", created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
                                    for c in contacts], p, mode="upsert")
    write_facts(engine, "deal", list(rows), p, mode="upsert")
    if excluded:
        write_facts(engine, "exclusion", [dict(entity="deal", entity_key=str(k), reason="технический тест",
                                               since=date(2026, 7, 1), decided_by="t") for k in excluded], p)
    finish_load(engine, p.load_id, rows_read=len(rows), rows_written=len(rows), status="ok")


def test_one_per_contact_counts_the_earliest_deal_only(engine):
    """Две сделки с флагом у одного контакта и одна у другого: без правила 3, с правилом 2."""
    setup_contacts(engine, [contact_deal(1, 10, 5), contact_deal(2, 10, 20), contact_deal(3, 11, 6)])
    assert new_first_sql(engine, CFG, "b1", *AUG).value == 3
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="one_per_contact").value == 2


def test_one_per_contact_keeps_the_deal_in_its_own_month(engine):
    """Первая сделка контакта — в июле, вторая — в августе: июль её засчитывает, август — нет."""
    setup_contacts(engine, [contact_deal(1, 10, 5, month=7), contact_deal(2, 10, 20)])
    assert new_first_sql(engine, CFG, "b1", *JUL, contour="one_per_contact").value == 1
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="one_per_contact").value == 0
    assert new_first_sql(engine, CFG, "b1", *AUG).value == 1


def test_one_per_contact_ignores_earlier_deal_that_is_not_a_lead(engine):
    """Место первой не занимает ни удалённая сделка, ни сделка без флага, ни технический тест."""
    gone = datetime(2026, 8, 6, tzinfo=timezone.utc)
    setup_contacts(engine, [contact_deal(1, 10, 5, deleted_at=gone), contact_deal(2, 10, 20),
                            contact_deal(3, 11, 5, new_first=False), contact_deal(4, 11, 20),
                            contact_deal(5, 12, 5), contact_deal(6, 12, 20)], excluded=[5])
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="one_per_contact").value == 3


def test_one_per_contact_breaks_a_tie_by_deal_id(engine):
    """Две сделки контакта созданы в одну секунду: засчитывается ровно одна, а не обе и не ни одной."""
    setup_contacts(engine, [contact_deal(1, 10, 5), contact_deal(2, 10, 5)])
    assert new_first_sql(engine, CFG, "b1", *AUG, contour="one_per_contact").value == 1


def test_one_per_contact_requires_new_first_base(engine):
    """«Одна на контакт» среди всех сделок компании — ошибка конфигурации, а не тихий другой расчёт."""
    setup_contacts(engine, [contact_deal(1, 10, 5)])
    with pytest.raises(RuleViolation) as e:
        new_first_sql(engine, CFG, "b1", *AUG, contour="one_per_contact_all")
    assert e.value.code == "К2" and "first_per_contact" in str(e.value)


def test_ladder_shows_the_contact_step(engine):
    """Лестница отбора называет шаг и его цену — и сходится с числом метрики."""
    from datacore.serve.explain import ladder
    setup_contacts(engine, [contact_deal(1, 10, 5), contact_deal(2, 10, 20), contact_deal(3, 11, 6)])
    steps = ladder(engine, CFG, "b1", *AUG, "one_per_contact")
    step = next(s for s in steps if s["name"] == "одна сделка на контакт")
    assert (step["value"], step["cut"]) == (2, 1)
    assert steps[-1]["value"] == new_first_sql(engine, CFG, "b1", *AUG, contour="one_per_contact").value
