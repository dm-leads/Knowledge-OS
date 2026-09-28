from datetime import date, datetime, timedelta, timezone

from datacore.checks.history import breakpoint_report, changes_by_day, fill_rate_by_day
from datacore.schema.config import load_config
from datacore.schema.migrate import migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import register_load, write_facts
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
MSK = timezone(timedelta(hours=3))
PROV = Provenance("crm_mirror", "D", "L-h", datetime(2026, 9, 14, 12, tzinfo=MSK))


def seed(engine):
    migrate(engine)
    register_load(engine, PROV, date(2026, 9, 14))
    write_facts(engine, "contact", [dict(contact_id=1, brand="b1", created_at=datetime(2026, 8, 1, tzinfo=MSK))], PROV)
    deals, changes, did = [], [], 0
    for day in range(20):
        d = date(2026, 8, 14) + timedelta(days=day)
        after = d >= date(2026, 8, 25)
        for i in range(10):
            did += 1
            filled = i < (6 if after else 2)
            deals.append(dict(deal_id=did, contact_id=1, brand="b1", pipeline="p", status="s", is_new_first=True,
                              created_at=datetime(d.year, d.month, d.day, 10, tzinfo=MSK), entry_channel_tech="calltracker" if filled else None))
        for k in range(4 if after else 1):
            changes.append(dict(deal_id=did, field="field_tech", changed_at=datetime(d.year, d.month, d.day, 11, k, tzinfo=MSK),
                                old_value=None, new_value="calltracker", recorded_at=PROV.loaded_at))
    write_facts(engine, "deal", deals, PROV)
    write_facts(engine, "deal_field_change", changes, PROV)


def test_fill_rate_and_changes_and_breakpoint(engine):
    seed(engine)
    rate = fill_rate_by_day(engine, CFG, "entry_channel_tech", date(2026, 8, 14), date(2026, 9, 3))
    assert rate[0] == (date(2026, 8, 14), 10, 2) and rate[-1] == (date(2026, 9, 2), 10, 6)
    assert changes_by_day(engine, "field_tech", date(2026, 8, 14), date(2026, 9, 3))[-1] == (date(2026, 9, 2), 4)
    r = breakpoint_report(engine, CFG, "entry_channel_tech", "field_tech", date(2026, 8, 24), days=10)
    assert r.ok and r.rate_before == 0.2 and r.rate_after == 0.6 and r.changes_before == 1 and r.changes_after == 4
