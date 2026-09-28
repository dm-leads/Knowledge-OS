from datetime import date, datetime, timedelta, timezone

import pytest

from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import FACT_TABLES, PRIMARY_KEYS, all_versions, applied_versions, migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import register_load, write_facts

MSK = timezone(timedelta(hours=3))
LOADED = datetime(2026, 9, 14, 12, 0, tzinfo=MSK)
P1 = Provenance("crm", "D", "L-1", LOADED)
P2 = Provenance("crm", "D", "L-2", LOADED + timedelta(hours=1))


def contact(cid, brand="b1"):
    return dict(contact_id=cid, brand=brand, created_at=datetime(2026, 9, 1, tzinfo=MSK))


@pytest.fixture
def db(engine):
    migrate(engine)
    for p in (P1, P2):
        register_load(engine, p, as_of=date(2026, 9, 14))
    return engine


def test_migration_0002_adds_updated_at(db):
    assert applied_versions(db) == all_versions()
    db.execute("SELECT updated_at FROM facts.deal")
    assert set(PRIMARY_KEYS) == set(FACT_TABLES)


def test_upsert_updates_existing_row_and_reports_read_back(db):
    write_facts(db, "contact", [contact(1), contact(2)], P1)
    report = write_facts(db, "contact", [contact(1, brand="b2"), contact(3)], P2, mode="upsert")
    assert (report.rows_written, report.rows_read_back) == (2, 2)
    assert db.fetchall("SELECT contact_id, brand, load_id FROM facts.contact ORDER BY contact_id") == [
        (1, "b2", "L-2"), (2, "b1", "L-1"), (3, "b1", "L-2")]


def test_upsert_collapses_duplicate_keys_in_batch_last_wins(db):
    report = write_facts(db, "contact", [contact(1, "b1"), contact(1, "b2")], P1, mode="upsert")
    assert report.rows_written == 1
    assert db.fetchone("SELECT brand FROM facts.contact WHERE contact_id = 1") == ("b2",)


def test_insert_mode_still_rejects_duplicates_with_k3(db):
    write_facts(db, "contact", [contact(1)], P1)
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "contact", [contact(1)], P1)
    assert e.value.code == "К3"
