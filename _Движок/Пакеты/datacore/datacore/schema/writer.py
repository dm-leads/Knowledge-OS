"""Единственная дверь для записи фактов: проверки К1, К5, К6 в коде, К3–К5 ограничениями базы; отчёт «записано N, прочитано N»."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .engine import ConstraintError
from .errors import RuleViolation
from .migrate import FACT_TABLES, PRIMARY_KEYS
from .pii import require_no_pii
from .provenance import Provenance

EVENT_TIME_COLUMNS = {"contact": "created_at", "deal": "created_at", "deal_field_change": "changed_at",
                      "payment": "paid_at", "shipment_cogs": "shipped_at", "visit": "started_at", "web_visit": "started_at",
                      "phone_call": "started_at", "order_marker": "ordered_at"}
EVENT_DATE_COLUMNS = {"web_daily": "day", "spend": "day", "demand": "month", "dialog_fact": "period_end",
                      "snapshot_number": "as_of"}
_KIND_TO_CODE = {"primary": "К3", "foreign": "К4", "check": "К5", "not_null": "К1"}
# Связи, которые проверяет писатель, а не база (миграция 0003): таблица → (колонка, родительская таблица, колонка родителя).
# Платежа здесь нет с этапа 4: деньги приходят по заказу, а заказ бывает вне digital-контура — сделки CRM у него
# просто не существует. Выбрасывать за это оплату нельзя, поэтому связь со сделкой не обязательна.
REFERENCES = {"deal_field_change": ("deal_id", "deal", "deal_id")}


@dataclass(frozen=True)
class WriteReport:
    table: str
    rows_written: int
    rows_read_back: int


def register_load(engine, prov: Provenance, as_of: date) -> None:
    engine.execute("INSERT INTO meta.load_log (load_id, source_system, source_class, as_of, started_at, status) "
                   "VALUES (?, ?, ?, ?, ?, 'running')",
                   (prov.load_id, prov.source_system, prov.source_class, as_of, datetime.now(timezone.utc)))
    engine.commit()


def finish_load(engine, load_id: str, *, rows_read: int, rows_written: int, status: str) -> None:
    if status not in ("ok", "failed"):
        raise ValueError("статус загрузки: ok или failed")
    engine.execute("UPDATE meta.load_log SET finished_at = ?, rows_read = ?, rows_written = ?, status = ? "
                   "WHERE load_id = ?", (datetime.now(timezone.utc), rows_read, rows_written, status, load_id))
    engine.commit()


def write_facts(engine, table: str, rows: list[dict], prov: Provenance, *, allowed_values=None,
                mode: str = "insert") -> WriteReport:
    """mode="insert" — только новые строки (дубль ключа → К3); mode="upsert" — строка с тем же ключом обновляется
    целиком, включая происхождение; дубли ключа внутри пачки схлопываются, последняя побеждает."""
    if mode not in ("insert", "upsert"):
        raise ValueError("mode: insert или upsert")
    if table not in FACT_TABLES:
        raise ValueError(f"«{table}» не таблица фактов: {FACT_TABLES}")
    if not rows:
        raise ValueError("нечего записывать: пустой список строк")
    keys = tuple(rows[0].keys())
    if any(tuple(r.keys()) != keys for r in rows):
        raise ValueError("строки с разным набором колонок")
    registered = engine.fetchone("SELECT source_system, source_class FROM meta.load_log WHERE load_id = ?",
                                 (prov.load_id,))
    if registered is None:
        raise RuleViolation("К1", f"загрузка «{prov.load_id}» не зарегистрирована в meta.load_log")
    if registered != (prov.source_system, prov.source_class):
        raise RuleViolation("К1", f"происхождение {(prov.source_system, prov.source_class)} не совпадает "
                                  f"с журналом загрузки «{prov.load_id}»: {registered}")
    require_no_pii(rows, allowed_values)
    ref = REFERENCES.get(table)
    if ref:
        column, parent, parent_column = ref
        wanted = {r[column] for r in rows if r.get(column) is not None}
        if wanted:
            known = {v for (v,) in engine.fetchall(f"SELECT {parent_column} FROM facts.{parent}")}
            missing = wanted - known
            if missing:
                raise RuleViolation("К4", f"facts.{table}: {len(missing)} ссылок на отсутствующие строки facts.{parent}")
    event_col, date_col = EVENT_TIME_COLUMNS.get(table), EVENT_DATE_COLUMNS.get(table)
    for i, row in enumerate(rows):
        ts = row.get(event_col) if event_col else None
        if ts is not None:
            if not isinstance(ts, datetime) or ts.tzinfo is None:
                raise RuleViolation("К5", f"строка {i}: «{event_col}» без пояса времени")
            if ts > prov.loaded_at:
                raise RuleViolation("К5", f"строка {i}: событие {ts.isoformat()} позже загрузки {prov.loaded_at.isoformat()}")
        d = row.get(date_col) if date_col else None
        if d is not None:
            if type(d) is not date:
                raise RuleViolation("К5", f"строка {i}: «{date_col}» должна быть датой")
            limit = prov.loaded_at.date() + (timedelta(days=1) if date_col == "period_end" else timedelta(0))
            if d > limit:
                raise RuleViolation("К5", f"строка {i}: «{date_col}» {d} позже дня загрузки {prov.loaded_at.date()}")
    key = PRIMARY_KEYS[table] if mode == "upsert" else None
    prov_cols = prov.as_columns()
    pk = PRIMARY_KEYS[table]
    if key:
        rows = list({tuple({**r, **prov_cols}[k] for k in key): r for r in rows}.values())   # ключ может включать source_system
    columns = keys + tuple(prov_cols.keys())
    batch_keys = {tuple({**r, **prov_cols}[k] for k in pk) for r in rows}
    engine.begin()                                    # вся пачка — одна транзакция: либо все строки, либо ни одной
    try:
        engine.bulk_insert(f"facts.{table}", columns,
                           [tuple(r[k] for k in keys) + tuple(prov_cols.values()) for r in rows], conflict_key=key)
    except ConstraintError as e:
        engine.rollback()
        raise RuleViolation(_KIND_TO_CODE.get(e.kind, "К3"), f"facts.{table}: {e}") from e
    # Чтение обратно — по ключам пачки: каждая строка пачки обязана лежать с номером этой загрузки. Разница счётчиков
    # до и после ломалась, когда в той же загрузке строку уже записали раньше (контакт из API, затем бренд из сделок).
    stored = {tuple(r) for r in engine.fetchall(f"SELECT {', '.join(pk)} FROM facts.{table} WHERE load_id = ?", (prov.load_id,))}
    read_back = len(batch_keys & stored)
    if read_back != len(batch_keys):
        engine.rollback()
        raise RuntimeError(f"facts.{table}: записано {len(batch_keys)}, прочитано обратно {read_back} — молчаливый провал")
    engine.commit()
    return WriteReport(table, len(batch_keys), read_back)
