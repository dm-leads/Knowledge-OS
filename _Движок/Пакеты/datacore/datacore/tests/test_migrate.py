from datacore.schema.migrate import (FACT_TABLES, PROVENANCE_COLUMNS, all_versions, applied_versions,
                                     migrate)


def columns(engine, schema, table):
    rows = engine.fetchall(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position", (schema, table))
    return {name: nullable for name, nullable in rows}


def test_migration_creates_all_tables_and_is_idempotent(engine):
    assert migrate(engine) == all_versions()
    assert applied_versions(engine) == all_versions()
    assert migrate(engine) == []                              # повтор — ничего не делает
    for table in FACT_TABLES:
        assert columns(engine, "facts", table), f"нет таблицы facts.{table}"
    assert columns(engine, "meta", "load_log") and columns(engine, "metrics", "number")


def test_every_fact_table_has_not_null_provenance(engine):
    migrate(engine)
    for table in FACT_TABLES:
        cols = columns(engine, "facts", table)
        for p in PROVENANCE_COLUMNS:
            assert cols.get(p) == "NO", f"facts.{table}.{p} должна быть NOT NULL (К1)"


_SNAPSHOTS: dict[str, dict] = {}     # колонки по движкам в пределах одного прогона


def test_same_columns_on_both_engines(engine):
    """Контракт Я10: набор колонок каждой таблицы одинаков на DuckDB и Postgres (сравнение в одном прогоне)."""
    migrate(engine)
    _SNAPSHOTS[engine.dialect] = {t: sorted(columns(engine, "facts", t)) for t in FACT_TABLES}
    if len(_SNAPSHOTS) == 2:
        assert _SNAPSHOTS["duckdb"] == _SNAPSHOTS["postgres"]



def test_migration_refuses_to_drop_a_table_with_facts(engine, tmp_path):
    """Ядро хранит историю, которую источники уже не отдают. Миграция с DROP непустой таблицы фактов не применяется,
    пока удаление не объявлено в файле явно (после переноса строк)."""
    import pytest
    from datacore.schema.migrate import MigrationRefused
    migrate(engine)
    engine.execute("CREATE TABLE facts.guard_probe (x INTEGER)")
    engine.execute("INSERT INTO facts.guard_probe VALUES (1)")
    engine.commit()
    extra = tmp_path / "m"
    extra.mkdir()
    import shutil
    from datacore.schema.migrate import MIGRATIONS_DIR
    for p in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(p, extra / p.name)
    nxt = max(all_versions()) + 1
    (extra / f"{nxt:04d}_drop_probe.sql").write_text("DROP TABLE IF EXISTS facts.guard_probe;\n", encoding="utf-8")
    with pytest.raises(MigrationRefused):
        migrate(engine, extra)
    assert engine.fetchone("SELECT COUNT(*) FROM facts.guard_probe")[0] == 1          # данные на месте
    (extra / f"{nxt:04d}_drop_probe.sql").write_text(
        "-- datacore: allow-drop facts.guard_probe\nDROP TABLE IF EXISTS facts.guard_probe;\n", encoding="utf-8")
    assert migrate(engine, extra) == [nxt]                                              # объявленное — проходит
