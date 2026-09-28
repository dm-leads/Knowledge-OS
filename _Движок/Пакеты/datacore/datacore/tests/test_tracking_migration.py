"""Этап 3: id визитов и звонков уникальны только внутри кабинета — ключ вместе с системой-источником."""
from datetime import datetime, timezone

import pytest

from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import PRIMARY_KEYS, migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import register_load, write_facts

NOW = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


def _prov(engine, system, load_id):
    p = Provenance(system, "C", load_id, NOW)
    register_load(engine, p, NOW.date())
    return p


def test_same_visit_and_call_id_in_two_systems_coexist(engine):
    migrate(engine)
    assert PRIMARY_KEYS["visit"] == ("visit_id", "source_system")
    assert PRIMARY_KEYS["phone_call"] == ("call_id", "source_system")
    at = datetime(2026, 9, 1, 7, tzinfo=timezone.utc)
    for system, load_id in (("sys_a", "L-a"), ("sys_b", "L-b")):
        p = _prov(engine, system, load_id)
        v = write_facts(engine, "visit", [dict(visit_id=10, started_at=at, marker="m", marker_level_1="g", landing_page="/",
                                               referrer_host=None, client_hash="ab" * 32, device="mobile", geo=None)], p, mode="upsert")
        c = write_facts(engine, "phone_call", [dict(call_id=1, started_at=at, visit_id=10, scenario="s", callee_line=None,
                                                    caller_hash="cd" * 32, answered=True, duration_s=60, deal_id=None)], p, mode="upsert")
        assert (v.rows_written, c.rows_written) == (1, 1)
    assert engine.fetchone("SELECT COUNT(*) FROM facts.visit WHERE visit_id = 10")[0] == 2
    assert engine.fetchone("SELECT COUNT(*) FROM facts.phone_call WHERE call_id = 1")[0] == 2


def test_order_marker_has_channel_group_column(engine):
    migrate(engine)
    cols = {c for (c,) in engine.fetchall("SELECT column_name FROM information_schema.columns "
                                          "WHERE table_schema = 'facts' AND table_name = 'order_marker'")}
    assert "marker_level_1" in cols


def test_duplicate_visit_in_same_system_is_k3(engine):
    migrate(engine)
    p = _prov(engine, "sys_a", "L-a")
    row = dict(visit_id=10, started_at=datetime(2026, 9, 1, tzinfo=timezone.utc), marker=None, marker_level_1=None,
               landing_page=None, referrer_host=None, client_hash=None, device=None, geo=None)
    write_facts(engine, "visit", [row], p)
    with pytest.raises(RuleViolation) as e:
        write_facts(engine, "visit", [row], p)
    assert e.value.code == "К3"
