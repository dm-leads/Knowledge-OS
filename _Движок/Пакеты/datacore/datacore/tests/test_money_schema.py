"""Этап 4: платёж хранит номер заказа и бренд; сделка может быть неизвестна — платёж всё равно факт."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import register_load, write_facts

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def _prov(engine):
    p = Provenance("ledger", "D", "L-money", NOW)
    register_load(engine, p, NOW.date())
    return p


def test_payment_keeps_order_and_brand(engine):
    migrate(engine)
    p = _prov(engine)
    row = dict(payment_id="pay-1", deal_id=101, order_id="ord-1", brand="b1",
               paid_at=datetime(2026, 8, 5, tzinfo=timezone.utc), revenue=Decimal("150000.00"),
               cogs=Decimal("90000.00"), money_system="ledger", has_basis=True)
    w = write_facts(engine, "payment", [row], p, mode="upsert")
    assert (w.rows_written, w.rows_read_back) == (1, 1)
    got = engine.fetchone("SELECT order_id, brand, revenue FROM facts.payment WHERE payment_id = 'pay-1'")
    assert got[0] == "ord-1" and got[1] == "b1" and Decimal(str(got[2])) == Decimal("150000.00")


def test_payment_without_known_deal_is_still_stored(engine):
    """Оплата есть, а сделки в фактах нет (заказ вне digital-контура): деньги не выбрасываются."""
    migrate(engine)
    p = _prov(engine)
    row = dict(payment_id="pay-2", deal_id=None, order_id="ord-2", brand="b2",
               paid_at=datetime(2026, 8, 6, tzinfo=timezone.utc), revenue=Decimal("1000.00"),
               cogs=None, money_system="ledger", has_basis=True)
    assert write_facts(engine, "payment", [row], p, mode="upsert").rows_written == 1


def test_payment_from_future_is_k5(engine):
    migrate(engine)
    p = _prov(engine)
    row = dict(payment_id="pay-3", deal_id=None, order_id="ord-3", brand="b1",
               paid_at=datetime(2030, 1, 1, tzinfo=timezone.utc), revenue=Decimal("1.00"),
               cogs=None, money_system="ledger", has_basis=True)
    with pytest.raises(RuleViolation) as e:
        write_facts(engine, "payment", [row], p)
    assert e.value.code == "К5"
