"""Миграции схемы: файлы NNNN_имя.sql применяются по порядку и записываются в meta.schema_version."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
FACT_TABLES = ("contact", "deal", "deal_field_change", "payment", "shipment_cogs", "visit", "web_visit", "web_visit_goal",
               "phone_call", "order_marker", "web_daily", "spend", "demand", "dialog_fact", "snapshot_number", "exclusion")
PROVENANCE_COLUMNS = ("source_system", "source_class", "load_id", "loaded_at")
PRIMARY_KEYS = {"contact": ("contact_id",), "deal": ("deal_id",), "deal_field_change": ("deal_id", "field", "changed_at"),
                "payment": ("payment_id", "source_system"), "shipment_cogs": ("shipment_id", "source_system"),
                "visit": ("visit_id", "source_system"), "web_visit": ("visit_id", "source_system"),
                "web_visit_goal": ("visit_id", "goal_id", "source_system"),
                "phone_call": ("call_id", "source_system"),
                "order_marker": ("deal_id", "source_system"), "web_daily": ("day", "landing_page", "goal", "source_system"),
                "spend": ("day", "campaign", "source_system"), "demand": ("month", "phrase", "region", "source_system"),
                "dialog_fact": ("fact_code", "value", "channel", "period_start", "period_end", "source_system"),
                "snapshot_number": ("metric", "scope", "flow", "segment", "period_start", "period_end", "as_of", "source_system"),
                "exclusion": ("entity", "entity_key")}
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def _files(migrations_dir: Path | None = None) -> list[tuple[int, str, Path]]:
    found = []
    for path in sorted(Path(migrations_dir or MIGRATIONS_DIR).glob("*.sql")):
        m = _NAME.match(path.name)
        if not m:
            raise ValueError(f"имя миграции не по шаблону NNNN_имя.sql: {path.name}")
        found.append((int(m.group(1)), m.group(2), path))
    return found


def all_versions(migrations_dir: Path | None = None) -> list[int]:
    """Номера всех миграций на диске. Тесты сверяются с ним, а не с записанным от руки списком: иначе каждая новая
    миграция ломала бы проверки, не имеющие к ней отношения (этап 5)."""
    return [v for v, _, _ in _files(migrations_dir)]


def applied_versions(engine) -> list[int]:
    engine.execute("CREATE SCHEMA IF NOT EXISTS meta")
    engine.execute("CREATE TABLE IF NOT EXISTS meta.schema_version (version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
                   "applied_at TIMESTAMP WITH TIME ZONE NOT NULL)")
    return [v for (v,) in engine.fetchall("SELECT version FROM meta.schema_version ORDER BY version")]


class MigrationRefused(RuntimeError):
    """Миграция удалила бы данные фактов — не применяется."""


_DROP = re.compile(r"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(facts\.[a-z_]+)", re.IGNORECASE | re.MULTILINE)
_ALLOW_DROP = re.compile(r"^--\s*datacore:\s*allow-drop\s+(facts\.[a-z_]+)\s*$", re.MULTILINE)


def _guard_drops(engine, sql: str, name: str) -> None:
    """Ядро — единственное место, где хранится история, которую источники уже не отдают (рекламный кабинет — только
    три года назад, журнал CRM — с 15.01.2025). Поэтому миграция не может удалить таблицу фактов, в которой есть строки: пересборка
    через DROP была безопасна, пока таблицы были пустыми (0009 — 23.09.2026), и однажды молча стёрла бы историю.
    Осознанное удаление с переносом данных объявляется в самом файле строкой «-- datacore: allow-drop facts.X» —
    её видно при проверке, и она не появится случайно."""
    allowed = set(_ALLOW_DROP.findall(sql))
    for table in _DROP.findall(sql):
        if table in allowed:
            continue
        schema, tbl = table.split(".")
        exists = engine.fetchone("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                                 (schema, tbl))[0]
        if exists and engine.fetchone(f"SELECT COUNT(*) FROM {table}")[0]:
            raise MigrationRefused(f"миграция {name} удаляет непустую таблицу {table} — данные были бы потеряны. "
                                   f"Перенесите строки и добавьте в файл «-- datacore: allow-drop {table}»")


def migrate(engine, migrations_dir: Path | None = None) -> list[int]:
    """Каждый файл миграции — одна транзакция вместе с записью версии: сбой посередине не оставляет половину DDL без
    отметки (в DuckDB без явной транзакции каждый оператор фиксировался сам — ревью Codex этапа 2, 15.09.2026)."""
    done = set(applied_versions(engine))
    engine.commit()
    applied = []
    for version, name, path in _files(migrations_dir):
        if version in done:
            continue
        engine.begin()
        try:
            sql = path.read_text(encoding="utf-8")
            _guard_drops(engine, sql, path.name)
            engine.script(sql)
            engine.execute("INSERT INTO meta.schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                           (version, name, datetime.now(timezone.utc)))
            engine.commit()
        except Exception:
            engine.rollback()
            raise
        applied.append(version)
    return applied
