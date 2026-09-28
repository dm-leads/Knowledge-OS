"""Когорта лидов: заявка и продажа — разные сделки, связь через контакт.

Числа проверяются тождествами, а не сравнением с записанным значением: если метрика изменится, тождество
сломается там, где нарушена арифметика, а не там, где обновилась цифра."""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from datacore.schema.config import load_config
from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import migrate
from datacore.schema.number import Status
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import (REGISTRY, ampu, ampu_per_lead, cogs_leads, gross_profit_leads,
                                    lead_conversion, revenue_leads)
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
AUG = (date(2026, 8, 1), date(2026, 9, 1))
MSK = timezone.utc

# Воронки: заявка падает в воронку первичных обращений, работа и оплата идут в воронке продаж.
LEADS_PIPELINE = "300"
SALES_PIPELINE = "100"


def deal(deal_id, contact_id, *, pipeline, is_new_first, day=1, brand="b1"):
    return dict(deal_id=deal_id, contact_id=contact_id, brand=brand, pipeline=pipeline, status="143",
                created_at=datetime(2026, 8, day, 10, tzinfo=MSK), first_sql_at=None,
                is_new_first=is_new_first, closed_at=None, price=None, entry_channel_tech=None,
                entry_channel_summary=None, marker=None, parent_deal_id=None, deleted_at=None,
                updated_at=None)


def contact(contact_id):
    return dict(contact_id=contact_id, brand="b1", created_at=datetime(2026, 7, 1, tzinfo=MSK),
                declared_source=None, phone_hash=None, email_hash=None, deleted_at=None)


def pay(pid, deal_id, day, rub, order_id):
    return dict(payment_id=pid, deal_id=deal_id, order_id=order_id, brand="b1",
                paid_at=datetime(2026, 8, day, 12, tzinfo=MSK), revenue=Decimal(str(rub)), cogs=None,
                has_basis=True, money_system="ledger")


def shipped(shipment_id, order_id, cogs_rub, sold_rub, day=3, total=1, priced=1, unpriced="0"):
    return dict(shipment_id=shipment_id, order_id=order_id, invoice_id=None, brand="b1",
                shipped_at=datetime(2026, 8, day, 11, tzinfo=MSK), cogs=Decimal(str(cogs_rub)),
                revenue_shipped=Decimal(str(sold_rub)), positions_total=total, positions_priced=priced,
                unpriced_revenue=Decimal(str(unpriced)), money_system="ledger")


def load(engine, *, contacts=(), deals=(), payments=(), shipments=(), as_of=date(2026, 9, 16)):
    """Факты CRM и денег вместе с окнами покрытия обеих систем."""
    from datacore.tests.helpers import cogs_system, log_money_window
    p_crm = Provenance(CFG.crm["system_mirror"], "D", f"C-{uuid4().hex[:8]}",
                       datetime(2026, 9, 16, 12, tzinfo=timezone.utc))
    register_load(engine, p_crm, as_of)
    if contacts:
        write_facts(engine, "contact", list(contacts), p_crm, mode="upsert")
    if deals:
        write_facts(engine, "deal", list(deals), p_crm, mode="upsert")
    finish_load(engine, p_crm.load_id, rows_read=len(deals), rows_written=len(deals), status="ok")

    p_money = Provenance("ledger", "D", f"M-{uuid4().hex[:8]}", datetime(2026, 9, 16, 12, tzinfo=timezone.utc))
    register_load(engine, p_money, as_of)
    payments = list(payments)
    if payments:
        write_facts(engine, "payment", payments, p_money, mode="upsert")
    if shipments:
        write_facts(engine, "shipment_cogs", list(shipments), p_money, mode="upsert")
    log_money_window(engine, CFG, date(2026, 8, 1), as_of, payments, p_money.load_id)
    log_money_window(engine, CFG, date(2026, 8, 1), as_of, list(shipments), p_money.load_id,
                     system=cogs_system(CFG))
    finish_load(engine, p_money.load_id, rows_read=len(payments), rows_written=len(payments), status="ok")


def simple(engine):
    """Два клиента-лида: первый заплатил, второй нет. Деньги идут по сделкам ДРУГОЙ воронки, без флага."""
    migrate(engine)
    load(engine,
         contacts=[contact(1), contact(2)],
         deals=[deal(101, 1, pipeline=LEADS_PIPELINE, is_new_first=True),     # заявка клиента 1
                deal(102, 2, pipeline=LEADS_PIPELINE, is_new_first=True),     # заявка клиента 2
                deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False)],   # продажа клиенту 1
         payments=[pay("p1", 201, 5, "100000.00", "ord-1")],
         shipments=[shipped("dem-1", "ord-1", "60000.00", "100000.00")])


def test_lead_and_sale_are_different_deals(engine):
    """Главный случай: у оплаченной сделки флага нет вовсе, и метрика всё равно находит её через контакт.

    Без перехода по контакту числитель был бы нулём: из 1 164 оплаченных сделок июн–авг 2026 флаг есть у четырёх."""
    simple(engine)
    n = lead_conversion(engine, CFG, "b1", *AUG)
    assert n.value == 0.5 and n.denominator_value == 2.0    # один из двух лидов дал оплату
    assert n.unit == "доля" and n.denominator == "лидов"


def test_cohort_identity_profit_is_revenue_minus_cogs(engine):
    """Тождество: валовая прибыль когорты = выручка когорты − себестоимость когорты.

    Обе части берутся из одной выборки заказов, иначе разность считалась бы по разным множествам."""
    simple(engine)
    r = revenue_leads(engine, CFG, "b1", *AUG)
    c = cogs_leads(engine, CFG, "b1", *AUG)
    g = gross_profit_leads(engine, CFG, "b1", *AUG)
    assert g.value == pytest.approx(r.value - c.value)
    assert (r.value, c.value, g.value) == (100000.0, 60000.0, 40000.0)


def test_cohort_identity_ampu_is_profit_over_leads(engine):
    """Тождество: AMPU на лид = валовая прибыль когорты ÷ число лидов."""
    simple(engine)
    g = gross_profit_leads(engine, CFG, "b1", *AUG)
    a = ampu_per_lead(engine, CFG, "b1", *AUG)
    assert a.denominator_value == 2.0
    assert a.value == pytest.approx(g.value / a.denominator_value)
    assert a.value == 20000.0                               # 40 000 ₽ прибыли на два лида


def test_ampu_per_lead_differs_from_ampu_per_deal(engine):
    """Два определения — два числа. Знаменатель у них разный: заявки против оплаченных сделок."""
    simple(engine)
    per_lead = ampu_per_lead(engine, CFG, "b1", *AUG)
    per_deal = ampu(engine, CFG, "b1", *AUG)
    assert per_lead.denominator == "лидов" and per_deal.denominator == "оплаченных сделок"
    assert per_lead.denominator_value == 2.0 and per_deal.denominator_value == 1.0
    # прибыль одна и та же, знаменатели разные — значит и числа разные ровно во столько же раз
    assert per_deal.value == pytest.approx(per_lead.value * 2)


def test_payment_of_a_stranger_is_not_in_the_cohort(engine):
    """Деньги клиента, который заявку не оставлял, в когорту не входят — иначе выручка завышена."""
    migrate(engine)
    load(engine,
         contacts=[contact(1), contact(9)],
         deals=[deal(101, 1, pipeline=LEADS_PIPELINE, is_new_first=True),
                deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False),
                deal(209, 9, pipeline=SALES_PIPELINE, is_new_first=False)],   # клиент без заявки
         payments=[pay("p1", 201, 5, "100000.00", "ord-1"),
                   pay("p9", 209, 6, "500000.00", "ord-9")],
         shipments=[shipped("dem-1", "ord-1", "60000.00", "100000.00"),
                    shipped("dem-9", "ord-9", "300000.00", "500000.00")])
    assert revenue_leads(engine, CFG, "b1", *AUG).value == 100000.0
    assert cogs_leads(engine, CFG, "b1", *AUG).value == 60000.0


def test_two_payments_of_one_order_do_not_double_cogs(engine):
    """Аванс и доплата по одному заказу: выручка складывается, себестоимость — нет."""
    migrate(engine)
    load(engine,
         contacts=[contact(1)],
         deals=[deal(101, 1, pipeline=LEADS_PIPELINE, is_new_first=True),
                deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False)],
         payments=[pay("p1", 201, 5, "40000.00", "ord-1"), pay("p2", 201, 20, "60000.00", "ord-1")],
         shipments=[shipped("dem-1", "ord-1", "60000.00", "100000.00")])
    assert revenue_leads(engine, CFG, "b1", *AUG).value == 100000.0
    assert cogs_leads(engine, CFG, "b1", *AUG).value == 60000.0       # не 120 000
    assert gross_profit_leads(engine, CFG, "b1", *AUG).value == 40000.0


def test_payment_without_order_is_revenue_but_not_cogs(engine):
    """Платёж без заказа — выручка, но себестоимости у него нет: товар к нему не привязан.

    Выдать ноль значило бы объявить продажу бесплатной, поэтому число становится оценкой и говорит, чего не хватает."""
    migrate(engine)
    load(engine,
         contacts=[contact(1)],
         deals=[deal(101, 1, pipeline=LEADS_PIPELINE, is_new_first=True),
                deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False)],
         payments=[pay("p1", 201, 5, "100000.00", "ord-1"),
                   dict(pay("p2", 201, 6, "30000.00", "ord-1"), order_id=None, payment_id="p2")],
         shipments=[shipped("dem-1", "ord-1", "60000.00", "100000.00")])
    r = revenue_leads(engine, CFG, "b1", *AUG)
    c = cogs_leads(engine, CFG, "b1", *AUG)
    assert r.value == 130000.0                      # обе оплаты — выручка
    assert c.value == 60000.0                       # себестоимость только у того, где есть заказ
    assert "30 000" in c.missing and "не привязан к заказу" in c.missing


def test_two_leads_of_one_client_do_not_double_money(engine):
    """У клиента две заявки: он остаётся ОДНИМ клиентом когорты, а его деньги считаются один раз.

    Иначе и выручка, и себестоимость выросли бы вдвое на ровном месте."""
    migrate(engine)
    load(engine,
         contacts=[contact(1)],
         deals=[deal(101, 1, pipeline=LEADS_PIPELINE, is_new_first=True, day=1),
                deal(102, 1, pipeline=LEADS_PIPELINE, is_new_first=True, day=9),   # вторая заявка того же клиента
                deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False)],
         payments=[pay("p1", 201, 5, "100000.00", "ord-1")],
         shipments=[shipped("dem-1", "ord-1", "60000.00", "100000.00")])
    a = ampu_per_lead(engine, CFG, "b1", *AUG)
    assert a.denominator_value == 1.0               # клиент один, хотя заявок две
    assert revenue_leads(engine, CFG, "b1", *AUG).value == 100000.0
    assert cogs_leads(engine, CFG, "b1", *AUG).value == 60000.0
    assert lead_conversion(engine, CFG, "b1", *AUG).value == 1.0


def test_one_sale_shared_by_two_lead_clients_is_counted_once(engine):
    """Сделка-продажа привязана к клиенту, который пришёл двумя путями: платёж всё равно один.

    Без отбора платежей по их номеру сумма вошла бы в выручку дважды."""
    migrate(engine)
    load(engine,
         contacts=[contact(1)],
         deals=[deal(101, 1, pipeline=LEADS_PIPELINE, is_new_first=True, day=1),
                deal(102, 1, pipeline=LEADS_PIPELINE, is_new_first=True, day=2),
                deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False),
                deal(202, 1, pipeline=SALES_PIPELINE, is_new_first=False)],
         payments=[pay("p1", 201, 5, "100000.00", "ord-1")],
         shipments=[shipped("dem-1", "ord-1", "60000.00", "100000.00")])
    assert revenue_leads(engine, CFG, "b1", *AUG).value == 100000.0
    assert gross_profit_leads(engine, CFG, "b1", *AUG).value == 40000.0


def test_lead_without_payment_lowers_ampu_but_not_revenue(engine):
    """Лид без оплаты входит в знаменатель и не входит в числитель: иначе AMPU завышен."""
    migrate(engine)
    load(engine,
         contacts=[contact(1), contact(2), contact(3)],
         deals=[deal(101, 1, pipeline=LEADS_PIPELINE, is_new_first=True),
                deal(102, 2, pipeline=LEADS_PIPELINE, is_new_first=True),
                deal(103, 3, pipeline=LEADS_PIPELINE, is_new_first=True),
                deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False)],
         payments=[pay("p1", 201, 5, "100000.00", "ord-1")],
         shipments=[shipped("dem-1", "ord-1", "60000.00", "100000.00")])
    a = ampu_per_lead(engine, CFG, "b1", *AUG)
    assert a.denominator_value == 3.0 and a.value == pytest.approx(40000.0 / 3)
    assert lead_conversion(engine, CFG, "b1", *AUG).value == pytest.approx(1 / 3)


def test_cohort_numbers_are_estimates_and_say_why(engine):
    """Число когорты — всегда оценка: клиент мог заплатить по заявке прошлого периода, и это сказано вслух."""
    simple(engine)
    for fn in (lead_conversion, revenue_leads, cogs_leads, gross_profit_leads, ampu_per_lead):
        n = fn(engine, CFG, "b1", *AUG)
        assert n.status is Status.ESTIMATE, fn.__name__
        assert "верхняя оценка" in n.missing, fn.__name__


def test_cohort_without_shipments_is_no_data_not_zero(engine):
    """Оплата есть, отгрузки нет: себестоимость неизвестна, и ноль был бы ложью."""
    migrate(engine)
    load(engine,
         contacts=[contact(1)],
         deals=[deal(101, 1, pipeline=LEADS_PIPELINE, is_new_first=True),
                deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False)],
         payments=[pay("p1", 201, 5, "100000.00", "ord-1")])
    assert revenue_leads(engine, CFG, "b1", *AUG).value == 100000.0   # выручка — факт сама по себе
    for fn in (cogs_leads, gross_profit_leads, ampu_per_lead):
        n = fn(engine, CFG, "b1", *AUG)
        assert n.value is None and n.status is Status.NO_DATA, fn.__name__


def test_empty_cohort_is_no_data_not_infinity(engine):
    """Лидов нет — делить не на что; это «нет данных», а не бесконечность."""
    migrate(engine)
    load(engine, contacts=[contact(1)],
         deals=[deal(201, 1, pipeline=SALES_PIPELINE, is_new_first=False)],
         payments=[pay("p1", 201, 5, "100000.00", "ord-1")])
    for fn in (lead_conversion, ampu_per_lead):
        n = fn(engine, CFG, "b1", *AUG)
        assert n.value is None and n.denominator_value == 0.0, fn.__name__


def test_cohort_metrics_reject_segments(engine):
    migrate(engine)
    for fn in (lead_conversion, revenue_leads, cogs_leads, gross_profit_leads, ampu_per_lead):
        with pytest.raises(RuleViolation):
            fn(engine, CFG, "b1", *AUG, segment="channel")


def test_cohort_metrics_are_in_registry():
    assert {"lead_conversion", "revenue_leads", "cogs_leads", "gross_profit_leads",
            "ampu_per_lead"} <= set(REGISTRY)
