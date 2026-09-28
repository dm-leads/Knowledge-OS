"""Себестоимость, валовая прибыль и средняя прибыль на сделку: неполнота названа, а не спрятана в нуле."""
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
from datacore.serve.metrics import REGISTRY, ampu, cogs, gross_profit
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
AUG = (date(2026, 8, 1), date(2026, 9, 1))


def pay(pid, deal_id, brand, day, rub, order_id=None, has_basis=True):
    return dict(payment_id=pid, deal_id=deal_id, order_id=order_id or f"ord-{pid}", brand=brand,
                paid_at=datetime(2026, 8, day, 12, tzinfo=timezone.utc), revenue=Decimal(str(rub)),
                cogs=None, money_system="ledger", has_basis=has_basis)


def shipped(order_id, brand, day, cogs_rub, sold_rub, total=1, priced=1, unpriced="0", shipment_id=None):
    return dict(shipment_id=shipment_id or f"dem-{order_id}-{day}", order_id=order_id, invoice_id=None, brand=brand,
                shipped_at=datetime(2026, 8, day, 11, tzinfo=timezone.utc),
                cogs=Decimal(str(cogs_rub)), revenue_shipped=Decimal(str(sold_rub)), positions_total=total,
                positions_priced=priced, unpriced_revenue=Decimal(str(unpriced)), money_system="ledger")


def load(engine, payments_rows, cogs_rows=(), as_of=date(2026, 9, 16), since=date(2026, 8, 1),
         cogs_read_ok=True):
    """Факты денег и себестоимости вместе с окнами покрытия обеих — как пишет настоящий загрузчик.

    `cogs_read_ok=False` изображает недоступный справочник: деньги прочитаны, себестоимость нет, и окно
    себестоимости не пишется (ревью Codex, п.3)."""
    from datacore.tests.helpers import cogs_system, log_money_window
    payments_rows, cogs_rows = list(payments_rows), list(cogs_rows)
    p = Provenance("ledger", "D", f"L-{uuid4().hex[:8]}", datetime(2026, 9, 16, 12, tzinfo=timezone.utc))
    register_load(engine, p, as_of)
    write_facts(engine, "payment", payments_rows, p, mode="upsert")
    if cogs_rows:
        write_facts(engine, "shipment_cogs", cogs_rows, p, mode="upsert")
    log_money_window(engine, CFG, since, as_of, payments_rows, p.load_id)
    if cogs_read_ok:
        log_money_window(engine, CFG, since, as_of, cogs_rows, p.load_id, system=cogs_system(CFG))
    finish_load(engine, p.load_id, rows_read=len(payments_rows), rows_written=len(payments_rows), status="ok")


def test_cogs_takes_orders_paid_in_period(engine):
    """Период задаётся датой денег, а не датой отгрузки: иначе прибыль одного месяца уменьшалась бы затратами другого."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00"),
          shipped("ord-2", "b1", 4, "40000.00", "70000.00")])   # отгрузка есть, денег в августе по ней нет
    n = cogs(engine, CFG, "b1", *AUG)
    assert n.value == 60000.0 and n.status is Status.FACT and n.unit == "₽"


def test_cogs_counts_order_once_for_several_payments(engine):
    """Аванс и доплата по одному заказу — одна себестоимость, иначе она задвоится."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "40000.00", order_id="ord-1"),
                  pay("p2", 101, "b1", 20, "60000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00")])
    assert cogs(engine, CFG, "b1", *AUG).value == 60000.0


def test_cogs_without_shipment_is_no_data_not_zero(engine):
    """Оплата есть, отгрузки нет: ноль рублей означал бы «продано даром», а не «ещё не отгружено»."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")])
    n = cogs(engine, CFG, "b1", *AUG)
    assert n.value is None and n.status is Status.NO_DATA and "отгрузок" in n.missing


def test_unpriced_positions_make_cogs_an_estimate(engine):
    """Позиции без закупочной цены названы суммой: иначе валовая прибыль завышена молча."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00", total=2, priced=1, unpriced="30000.00")])
    n = cogs(engine, CFG, "b1", *AUG)
    assert n.value == 60000.0 and n.status is Status.ESTIMATE
    assert "30 000 ₽" in n.missing


def test_paid_but_unshipped_order_is_named(engine):
    """Аванс за товар, который уедет в следующем месяце: его себестоимости ещё нет, и молчать об этом нельзя —
    иначе из выручки вычтется себестоимость только части проданного, и валовая прибыль завышена."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1"),
                  pay("p2", 102, "b1", 6, "80000.00", order_id="ord-2")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00")])
    n = cogs(engine, CFG, "b1", *AUG)
    assert n.value == 60000.0 and n.status is Status.ESTIMATE
    assert "без отгрузки 1" in n.missing and "80 000 ₽" in n.missing


def test_several_shipments_of_one_order_are_summed(engine):
    """Заказ отгружен частями: это одна продажа, и себестоимость всех её отгрузок складывается."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "40000.00", "60000.00", shipment_id="dem-a"),
          shipped("ord-1", "b1", 9, "20000.00", "40000.00", shipment_id="dem-b")])
    assert cogs(engine, CFG, "b1", *AUG).value == 60000.0


def test_cogs_follows_payment_brand_not_shipment_brand(engine):
    """Кабинет берётся у платежа: у заказа вне выборки бренда нет, а деньги по нему уже отнесены кабинетом."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", None, 3, "60000.00", "100000.00")])
    assert cogs(engine, CFG, "b1", *AUG).value == 60000.0


def test_cogs_shipped_outside_the_period_is_named(engine):
    """Заказ оплачен в августе, а отгружен в июле: его себестоимость вся относится к этой продаже, но доля,
    уехавшая вне периода, обязана быть названа.

    Живая доля 21.09.2026 — 23,6 % числа августа. Без пояснения читатель принял бы число за «затраты месяца»."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "40000.00", "60000.00", shipment_id="dem-aug"),
          # вторая отгрузка того же заказа — в июле, вне периода метрики
          dict(shipped("ord-1", "b1", 3, "20000.00", "40000.00", shipment_id="dem-jul"),
               shipped_at=datetime(2026, 7, 20, 11, tzinfo=timezone.utc))])
    n = cogs(engine, CFG, "b1", *AUG)
    assert n.value == 60000.0                       # себестоимость заказа вся
    assert "отгружено вне периода 20 000 ₽" in n.missing
    assert "33 %" in n.missing


def test_gross_profit_is_revenue_minus_cogs(engine):
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00")])
    n = gross_profit(engine, CFG, "b1", *AUG)
    assert n.value == 40000.0 and n.status is Status.FACT


def test_gross_profit_is_estimate_when_a_part_is_estimate(engine):
    """Вычесть оценку из факта и назвать результат фактом нельзя."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00", total=2, priced=1, unpriced="30000.00")])
    assert gross_profit(engine, CFG, "b1", *AUG).status is Status.ESTIMATE


def test_gross_profit_without_cogs_is_no_data(engine):
    """Без себестоимости валовой прибыли нет: выдать за неё выручку — молчаливая ложь."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")])
    n = gross_profit(engine, CFG, "b1", *AUG)
    assert n.value is None and n.status is Status.NO_DATA


def test_ampu_divides_profit_by_paid_deals_and_keeps_denominator(engine):
    """Знаменатель не просто назван, а сохранён числом: доля обязана его иметь."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1"),
                  pay("p2", 102, "b1", 6, "60000.00", order_id="ord-2")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00"),
          shipped("ord-2", "b1", 4, "40000.00", "60000.00")])
    n = ampu(engine, CFG, "b1", *AUG)
    assert n.value == 30000.0               # (160 000 − 100 000) ÷ 2 сделки
    assert n.denominator_value == 2.0 and n.denominator == "оплаченных сделок"


def test_ampu_without_deals_is_no_data_not_infinity(engine):
    """Деньги пришли по заказам без сделки CRM: делить не на что, и это не бесконечность."""
    migrate(engine)
    load(engine, [pay("p1", None, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00")])
    n = ampu(engine, CFG, "b1", *AUG)
    assert n.value is None and n.status is Status.NO_DATA and n.denominator_value == 0.0


def test_money_metrics_reject_segments(engine):
    migrate(engine)
    for fn in (cogs, gross_profit, ampu):
        with pytest.raises(RuleViolation):
            fn(engine, CFG, "b1", *AUG, segment="channel")


def test_new_metrics_are_in_registry():
    assert {"cogs", "gross_profit", "ampu"} <= set(REGISTRY)


def test_unread_cogs_does_not_pass_as_fact(engine):
    """Справочник недоступен, себестоимость не прочитана — метрика не выдаёт старые строки за свежий факт.

    Ревью Codex этапа 5, п.3: сбой уходил только в примечание отчёта, а в фактах следа не оставлял. Метрика
    проверяла покрытие ДЕНЕГ, которые прочитались успешно, и отвечала «факт» по устаревшей себестоимости —
    прибыль оказывалась завышенной молча."""
    migrate(engine)
    load(engine, [pay("p1", 101, "b1", 5, "100000.00", order_id="ord-1")],
         [shipped("ord-1", "b1", 3, "60000.00", "100000.00")],
         cogs_read_ok=False)
    n = cogs(engine, CFG, "b1", *AUG)
    assert n.value is None and n.status is Status.NO_DATA, n
    assert "покрытие" in n.missing or "не покрыт" in n.missing, n.missing
    # выручка при этом остаётся фактом: деньги прочитаны успешно
    from datacore.serve.metrics import revenue
    assert revenue(engine, CFG, "b1", *AUG).value == 100000.0
