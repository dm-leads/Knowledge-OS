"""Один интерфейс к хранилищу для DuckDB и Postgres (Я10). Плейсхолдер — «?». Ошибки ограничений — ConstraintError."""
from __future__ import annotations

import re
from urllib.parse import urlparse


class ConstraintError(Exception):
    def __init__(self, kind: str, message: str, constraint: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.constraint = constraint


def connect(url: str, read_only: bool = False):
    """read_only — соединение только на чтение (агентский интерфейс, К8): DuckDB открывает файл в режиме чтения,
    Postgres переводит сессию в default_transaction_read_only."""
    parsed = urlparse(url)
    if parsed.scheme == "duckdb":
        import duckdb
        path = url[len("duckdb:///"):] or ":memory:"
        if path == ":memory:":
            con = duckdb.connect(path)
        elif read_only:
            # Только чтение для агента (К8): без доступа к файлам через read_text/read_csv/ATTACH — иначе запрос читает
            # любой файл на диске, включая файл секретов (ревью этапа 2, 15.09.2026); настройку нельзя вернуть запросом.
            con = duckdb.connect(path, read_only=True, config={"enable_external_access": False, "lock_configuration": True})
        else:
            con = duckdb.connect(path)
        return Engine("duckdb", con, url)
    if parsed.scheme in ("postgresql", "postgres"):
        import psycopg
        con = psycopg.connect(url, autocommit=False)
        if read_only:
            con.execute("SET default_transaction_read_only = on")
            con.commit()
        return Engine("postgres", con, url)
    raise ValueError(f"неизвестная схема URL хранилища: {parsed.scheme!r}")


class Engine:
    def __init__(self, dialect: str, con, url: str = ""):
        self.dialect = dialect
        self.con = con
        self.url = url

    # --- запросы ---
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def execute(self, sql: str, params=()) -> None:
        try:
            if self.dialect == "postgres":
                self.con.execute(self._sql(sql), tuple(params) or None)
            else:
                self.con.execute(sql, list(params))
        except Exception as e:                       # noqa: BLE001 — отображаем в ConstraintError
            raise self._map(e) from e

    def executemany(self, sql: str, rows) -> None:
        rows = [tuple(r) for r in rows]
        if not rows:
            return
        try:
            if self.dialect == "postgres":
                with self.con.cursor() as cur:
                    cur.executemany(self._sql(sql), rows)
            else:
                self.con.executemany(sql, rows)
        except Exception as e:                       # noqa: BLE001
            raise self._map(e) from e

    def bulk_insert(self, table: str, columns: tuple[str, ...], rows: list[tuple],
                    conflict_key: tuple[str, ...] | None = None) -> None:
        """Пакетная вставка. conflict_key — upsert: конфликт по ключу обновляет все неключевые колонки.
        DuckDB: пачка передаётся таблицей в памяти одним INSERT … SELECT — построчный executemany в DuckDB даёт
        ~2,7 мс на строку (154 тыс. сделок не легли за 2 часа, 14.09.2026), таблица — 0,6 с. Postgres — executemany."""
        if not rows:
            return
        cols = ", ".join(columns)
        tail = ""
        if conflict_key:
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in conflict_key)
            tail = f" ON CONFLICT ({', '.join(conflict_key)}) DO UPDATE SET {updates}"
        if self.dialect == "postgres":
            self.executemany(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in columns)}){tail}", rows)
            return
        import pandas as pd
        from decimal import Decimal
        # DuckDB выводит тип колонки таблицы в памяти по выборке значений: Decimal("1500") даёт DECIMAL(6,0), и сумма
        # 2 113 920 дальше в той же пачке не влезает (живой прогон 15.09.2026). Поэтому деньги едут строкой, а в SELECT
        # каждая колонка явно приводится к типу целевой таблицы.
        rows = [tuple(str(v) if isinstance(v, Decimal) else v for v in r) for r in rows]
        schema, _, name = table.partition(".")
        types = dict(self.fetchall("SELECT column_name, data_type FROM information_schema.columns "
                                   "WHERE table_schema = ? AND table_name = ?", (schema, name)))
        select = ", ".join(f"CAST({c} AS {types[c]}) AS {c}" if c in types else c for c in columns)
        frame = pd.DataFrame(rows, columns=list(columns), dtype=object)
        self.con.register("_datacore_batch", frame)
        try:
            self.execute(f"INSERT INTO {table} ({cols}) SELECT {select} FROM _datacore_batch{tail}")
        finally:
            self.con.unregister("_datacore_batch")

    def fetchall(self, sql: str, params=()) -> list[tuple]:
        if self.dialect == "postgres":
            return [tuple(r) for r in self.con.execute(self._sql(sql), tuple(params) or None).fetchall()]
        return [tuple(r) for r in self.con.execute(sql, list(params)).fetchall()]

    def fetchone(self, sql: str, params=()):
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def script(self, sql: str) -> None:
        """Несколько операторов через «;». Комментарии «--» отбрасываются до разбиения: точка с запятой
        внутри комментария — не граница оператора (в строковых литералах «--» не используем)."""
        lines = []
        for line in sql.splitlines():
            head, sep, _ = line.partition("--")
            lines.append(head if sep and head.count("'") % 2 == 0 else line)
        for statement in (s.strip() for s in "\n".join(lines).split(";")):
            if statement:
                self.execute(statement)

    # --- транзакции и жизнь соединения ---
    # DuckDB: commit() без транзакции — пустая операция, rollback() без транзакции — исключение
    # (проверено 14.09.2026, duckdb 1.5.5); psycopg открывает транзакцию неявно. Поэтому begin() явный,
    # rollback() терпимый.
    def begin(self) -> None:
        if self.dialect == "duckdb":
            self.con.begin()

    def commit(self) -> None:
        self.con.commit()

    def rollback(self) -> None:
        if getattr(self, "_closed", False):
            return
        try:
            self.con.rollback()
        except Exception as e:                       # noqa: BLE001
            if "no transaction is active" not in str(e):
                raise

    def close(self) -> None:
        """Идемпотентно: повторное закрытие (например, фикстурой после загрузчика) — не ошибка."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        (self.rollback if exc_type else self.commit)()
        self.close()

    # --- отображение ошибок ---
    def _map(self, e: Exception) -> Exception:
        if self.dialect == "postgres":
            import psycopg.errors as pe
            kinds = ((pe.UniqueViolation, "primary"), (pe.ForeignKeyViolation, "foreign"),
                     (pe.CheckViolation, "check"), (pe.NotNullViolation, "not_null"))
            for cls, kind in kinds:
                if isinstance(e, cls):
                    return ConstraintError(kind, str(e), getattr(e.diag, "constraint_name", None))
            return e
        text = str(e)
        if "ConstraintException" in type(e).__name__ or "Constraint Error" in text:
            low = text.lower()
            kind = ("primary" if "primary key" in low or "unique" in low else
                    "foreign" if "foreign key" in low else
                    "check" if "check constraint" in low else
                    "not_null" if "not null" in low else "other")
            m = re.search(r"\b(k\d+_[a-z_]+)\b", low)
            return ConstraintError(kind, text, m.group(1) if m else None)
        return e
