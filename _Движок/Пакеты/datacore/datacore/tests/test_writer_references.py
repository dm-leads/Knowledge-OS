"""Регрессия ревью этапа 2 (15.09.2026): в DuckDB повторный upsert сделки, на которую ссылается история изменений или
оплата, падал на внешнем ключе — со второго прогона инкремент сделок перестал бы работать. После миграции 0003 связь
проверяет писатель (К4), а повторная запись сделки и контакта проходит на обоих движках."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import all_versions, applied_versions, migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import register_load, write_facts

MSK = timezone(timedelta(hours=3))
P1 = Provenance("crm", "D", "L-ref-1", datetime(2026, 9, 14, 12, tzinfo=MSK))
P2 = Provenance("crm", "D", "L-ref-2", datetime(2026, 9, 15, 12, tzinfo=MSK))


def deal(status="new"):
    return dict(deal_id=10, contact_id=1, brand="b1", pipeline="p", status=status, created_at=datetime(2026, 9, 2, tzinfo=MSK))


@pytest.fixture
def db(engine):
    assert migrate(engine)[-1] == all_versions()[-1]
    for p in (P1, P2):
        register_load(engine, p, as_of=p.loaded_at.date())
    write_facts(engine, "contact", [dict(contact_id=1, brand="b1", created_at=datetime(2026, 9, 1, tzinfo=MSK))], P1)
    write_facts(engine, "deal", [deal()], P1)
    write_facts(engine, "deal_field_change", [dict(deal_id=10, field="f", changed_at=datetime(2026, 9, 3, tzinfo=MSK),
                                                  old_value=None, new_value="x", recorded_at=P1.loaded_at)], P1)
    write_facts(engine, "payment", [dict(payment_id="pay-1", deal_id=10, paid_at=datetime(2026, 9, 4, tzinfo=MSK),
                                        revenue=Decimal("1000.00"), money_system="m", has_basis=True)], P1)
    return engine


def test_deal_and_contact_upsert_after_history_and_payment(db):
    report = write_facts(db, "deal", [deal(status="won")], P2, mode="upsert")
    assert report.rows_written == 1
    write_facts(db, "contact", [dict(contact_id=1, brand="b2", created_at=datetime(2026, 9, 1, tzinfo=MSK))], P2, mode="upsert")
    assert db.fetchone("SELECT status, load_id FROM facts.deal WHERE deal_id = 10") == ("won", "L-ref-2")
    assert db.fetchone("SELECT COUNT(*) FROM facts.deal_field_change") == (1,) and db.fetchone("SELECT COUNT(*) FROM facts.payment") == (1,)


def test_history_for_unknown_deal_is_k4_from_writer(db):
    """Изменение поля несуществующей сделки — бессмыслица: менять нечего."""
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "deal_field_change", [dict(deal_id=999, field="f", changed_at=datetime(2026, 9, 3, tzinfo=MSK),
                                                  old_value=None, new_value="x", recorded_at=P2.loaded_at)], P2)
    assert e.value.code == "К4"


def test_payment_for_unknown_deal_is_stored(db):
    """Этап 4: деньги пришли по заказу вне digital-контура — сделки CRM у него нет. Это не нарушение связи, а
    обычное дело: отказ записывать такие платежи сделал бы выручку компании заведомо неполной."""
    report = write_facts(db, "payment", [dict(payment_id="pay-2", deal_id=999, paid_at=datetime(2026, 9, 4, tzinfo=MSK),
                                              revenue=Decimal("1.00"), money_system="m", has_basis=True)], P2)
    assert report.rows_written == 1
    assert db.fetchone("SELECT deal_id FROM facts.payment WHERE payment_id = 'pay-2'") == (999,)


def test_migration_0003_keeps_existing_rows(engine):
    migrate(engine)
    assert applied_versions(engine) == all_versions()
    assert migrate(engine) == []
