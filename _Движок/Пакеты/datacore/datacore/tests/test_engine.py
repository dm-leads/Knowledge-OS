import pytest

from datacore.schema.engine import ConstraintError, connect


def test_duckdb_memory_roundtrip():
    with connect("duckdb:///:memory:") as e:
        assert e.dialect == "duckdb"
        e.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v VARCHAR)")
        e.executemany("INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b")])
        assert e.fetchall("SELECT v FROM t WHERE id > ? ORDER BY id", (1,)) == [("b",)]


def test_constraint_error_is_mapped(engine):
    engine.execute("CREATE TABLE t_pk (id INTEGER PRIMARY KEY)")
    engine.execute("INSERT INTO t_pk VALUES (1)")
    with pytest.raises(ConstraintError) as err:
        engine.execute("INSERT INTO t_pk VALUES (1)")
    assert err.value.kind == "primary"
    engine.rollback()


def test_both_engines_run_same_script(engine):
    engine.script("CREATE SCHEMA IF NOT EXISTS s1; CREATE TABLE s1.t (d DATE, ts TIMESTAMP WITH TIME ZONE, x FLOAT8);")
    assert engine.fetchone("SELECT COUNT(*) FROM s1.t") == (0,)


def test_bulk_insert_upsert_with_late_values_on_both_engines(engine):
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    engine.script("CREATE SCHEMA IF NOT EXISTS s1; CREATE TABLE s1.b (id BIGINT PRIMARY KEY, parent BIGINT, price DECIMAL(18,2), "
                  "happened_at TIMESTAMP WITH TIME ZONE, tag VARCHAR);")
    t0 = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=3)))
    rows = [(57_000_000 + i, None if i < 2990 else 58_000_001, None if i < 2995 else Decimal("124010.55"),
             None if i < 2998 else t0, None if i < 2999 else "seo") for i in range(3000)]
    cols = ("id", "parent", "price", "happened_at", "tag")
    engine.begin(); engine.bulk_insert("s1.b", cols, rows); engine.commit()
    assert engine.fetchone("SELECT COUNT(*), COUNT(parent), MAX(parent), COUNT(price), MAX(price), COUNT(happened_at), COUNT(tag) FROM s1.b") == \
        (3000, 10, 58_000_001, 5, Decimal("124010.55"), 2, 1)
    engine.begin(); engine.bulk_insert("s1.b", cols, [(57_000_000, None, None, None, "upd")], conflict_key=("id",)); engine.commit()
    assert engine.fetchone("SELECT COUNT(*), MAX(tag) FROM s1.b WHERE id = 57000000") == (1, "upd")
    with pytest.raises(ConstraintError) as err:
        engine.begin(); engine.bulk_insert("s1.b", cols, [(57_000_001, None, None, None, None)])
    assert err.value.kind == "primary"
    engine.rollback()


def test_bulk_insert_money_of_growing_precision_in_one_batch(engine):
    """Регрессия живого прогона 15.09.2026: первая сумма 1500 задавала DECIMAL(6,0), сумма в миллионы дальше не влезала."""
    from decimal import Decimal
    engine.script("CREATE SCHEMA IF NOT EXISTS s1; CREATE TABLE s1.m (id BIGINT PRIMARY KEY, price DECIMAL(18,2), flag BOOLEAN);")
    rows = [(i, Decimal("1500") if i < 5000 else Decimal("2113920"), i % 2 == 0) for i in range(6000)] + [(6000, Decimal("0.55"), None)]
    engine.begin(); engine.bulk_insert("s1.m", ("id", "price", "flag"), rows); engine.commit()
    assert engine.fetchone("SELECT COUNT(*), MAX(price), MIN(price), COUNT(flag) FROM s1.m") == (6001, Decimal("2113920.00"), Decimal("0.55"), 6000)
