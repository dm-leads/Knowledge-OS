from datetime import date, datetime, timedelta, timezone

import pytest

from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import register_load, write_facts

MSK = timezone(timedelta(hours=3))
LOADED = datetime(2026, 9, 14, 12, 0, tzinfo=MSK)
PROV = Provenance(source_system="crm", source_class="D", load_id="L-2026-09-14-1", loaded_at=LOADED)


def contact(cid=1, **kw):
    row = dict(contact_id=cid, brand="b1", created_at=datetime(2026, 9, 1, 10, 0, tzinfo=MSK))
    row.update(kw)
    return row


def deal(did=10, cid=1, **kw):
    row = dict(deal_id=did, contact_id=cid, brand="b1", pipeline="p", status="new",
               created_at=datetime(2026, 9, 2, 10, 0, tzinfo=MSK))
    row.update(kw)
    return row


@pytest.fixture
def db(engine):
    migrate(engine)
    register_load(engine, PROV, as_of=date(2026, 9, 14))
    return engine


def test_happy_path_reports_written_equals_read_back(db):
    report = write_facts(db, "contact", [contact(1), contact(2)], PROV)
    assert (report.rows_written, report.rows_read_back) == (2, 2)
    assert db.fetchone("SELECT COUNT(*) FROM facts.contact WHERE load_id = ?", (PROV.load_id,)) == (2,)


def test_k1_provenance_incomplete():
    with pytest.raises(RuleViolation) as e:
        Provenance(source_system="", source_class="D", load_id="L", loaded_at=LOADED)
    assert e.value.code == "К1"
    with pytest.raises(RuleViolation) as e:
        Provenance(source_system="crm", source_class="Z", load_id="L", loaded_at=LOADED)
    assert e.value.code == "К1"
    with pytest.raises(RuleViolation) as e:
        Provenance(source_system="crm", source_class="D", load_id="L", loaded_at=LOADED.replace(tzinfo=None))
    assert e.value.code == "К1"


def test_k1_unregistered_load_is_rejected(engine):
    migrate(engine)
    with pytest.raises(RuleViolation) as e:
        write_facts(engine, "contact", [contact()], PROV)      # register_load не вызывали
    assert e.value.code == "К1"


def test_k1_provenance_must_match_load_log(db):
    other = Provenance(source_system="web", source_class="A", load_id=PROV.load_id, loaded_at=LOADED)
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "contact", [contact()], other)         # тот же load_id, другая система и класс
    assert e.value.code == "К1"


@pytest.mark.parametrize("table,row", [
    ("web_daily", dict(day=date(2026, 9, 15), landing_page="/", visits=1)),
    ("snapshot_number", dict(metric="m", scope="s", period_start=date(2026, 8, 1), period_end=date(2026, 9, 1),
                             as_of=date(2026, 9, 15), value=1.0)),
])
def test_k5_date_from_future_in_daily_and_snapshot_tables(db, table, row):
    with pytest.raises(RuleViolation) as e:
        write_facts(db, table, [row], PROV)                    # загрузка 14.09, дата 15.09
    assert e.value.code == "К5"


def test_k6_long_free_text_is_rejected(db):
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "contact", [contact(declared_source="т" * 201)], PROV)
    assert e.value.code == "К6"


def test_k3_duplicate_key(db):
    write_facts(db, "contact", [contact(1)], PROV)
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "contact", [contact(1)], PROV)
    assert e.value.code == "К3"


def test_k4_deal_without_contact(db):
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "deal", [deal(cid=999)], PROV)
    assert e.value.code == "К4"


def test_k5_event_from_future_and_naive_time(db):
    write_facts(db, "contact", [contact(1)], PROV)
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "deal", [deal(created_at=LOADED + timedelta(days=1))], PROV)
    assert e.value.code == "К5"
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "deal", [deal(created_at=datetime(2026, 9, 2, 10, 0))], PROV)
    assert e.value.code == "К5"


def test_k6_phone_in_text_column(db):
    with pytest.raises(RuleViolation) as e:
        write_facts(db, "contact", [contact(declared_source="позвонил с 89990000000")], PROV)
    assert e.value.code == "К6"


def test_unknown_table_and_ragged_rows_are_rejected(db):
    with pytest.raises(ValueError):
        write_facts(db, "raw_stuff", [contact()], PROV)
    with pytest.raises(ValueError):
        write_facts(db, "contact", [contact(1), {"contact_id": 2}], PROV)
