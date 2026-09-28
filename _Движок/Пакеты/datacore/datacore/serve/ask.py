"""Агент ядра: вопрос → число со статусом или SELECT только на чтение (К8). Деньги считает код, агент читает."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from datacore.schema.config import load_config
from datacore.schema.engine import connect
from datacore.serve.storage import storage_url
from datacore.schema.errors import RuleViolation
from datacore.schema.number import Number
from .metrics import REGISTRY

_WRITE = re.compile(r"\b(insert|update|delete|merge|drop|alter|create|truncate|copy|attach|grant|call|load|install|set|pragma)\b", re.I)


_HIDDEN = re.compile(r"\braw\.|_dlt", re.I)   # сырой слой наружу не публикуется (стандарт, раздел 2 и 6)
# Файловые и серверные функции: DuckDB (read_*, glob …) и Postgres (pg_read_file, pg_ls_dir, lo_import, dblink …) —
# в Postgres запрет соединения на чтение файлы не закрывает (ревью Codex этапа 2); роль без прав — на серверной стадии.
_FILE_FUNCTIONS = re.compile(r"\b(read_\w+|glob|sniff_csv|parquet_\w+|iceberg_\w+|delta_scan|query_table|pg_read_file|"
                             r"pg_read_binary_file|pg_ls_\w+|pg_stat_file|lo_import|lo_export|lo_get|dblink\w*|"
                             r"set_config|pg_terminate_backend|pg_cancel_backend)\s*\(", re.I)
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _normalized(sql: str) -> str:
    """Для проверок объектов: без комментариев, кавычек идентификаторов и пробелов вокруг точки. Иначе
    SELECT * FROM "raw"."contacts" или raw /**/ . contacts обходили запрет (ревью этапа 2, 15.09.2026)."""
    text = _COMMENTS.sub(" ", sql).replace('"', "").replace("`", "")
    return re.sub(r"\s*\.\s*", ".", text)


def check_read_only(sql: str) -> str:
    """К8: один SELECT или WITH без операторов записи и без второго оператора; слой raw и файлы на диске агенту не видны."""
    text = sql.strip().rstrip(";").strip()
    if ";" in text or not re.match(r"^(select|with)\b", text, re.I) or _WRITE.search(text):
        raise RuleViolation("К8", "через ask доступно только чтение: один SELECT или WITH без операторов записи")
    norm = _normalized(text)
    if _HIDDEN.search(norm):
        raise RuleViolation("К8", "через ask доступны только facts.* и metrics.*: сырой слой raw наружу не публикуется")
    if _FILE_FUNCTIONS.search(norm):
        raise RuleViolation("К8", "через ask нельзя читать файлы: только таблицы facts.* и metrics.*")
    return text


def run_sql(url: str, sql: str, limit: int = 100) -> list[tuple]:
    text = check_read_only(sql)
    engine = connect(url, read_only=True)
    try:
        return engine.fetchall(f"SELECT * FROM ({text}) AS q LIMIT {int(limit)}")
    finally:
        engine.close()


def render(n: Number) -> str:
    if n.value is None:
        value, unit = "нет данных", n.unit
    elif n.unit == "шт":
        value, unit = f"{n.value:.0f}", n.unit
    elif n.unit == "доля":
        # Доля показывается процентами и дробью: «0.0049152174342170195» нечитаемо, а «0,49 %» нельзя проверить.
        # «49 из 10000 visits» — проверяемый факт (ревью Codex этапа 5, п.7). Доля без знаменателя не факт (К2).
        share = f"{n.value * 100:.2f} %".replace(".", ",")
        if n.denominator_value:
            whole = f"{n.denominator_value:,.0f}".replace(",", " ")
            part = f"{n.value * n.denominator_value:,.0f}".replace(",", " ")
            value, unit = share, f"({part} из {whole} {n.denominator})"
        else:
            value, unit = share, f"от «{n.denominator}»"
    else:
        value, unit = f"{n.value:,.0f}".replace(",", " "), n.unit
        # Среднее — тоже дробь, и знаменатель у него такое же условие проверяемости, как у доли: «300 ₽»
        # ничего не говорит, «300 ₽ (на 2 оплаченных сделки)» проверяется умножением.
        if n.denominator_value:
            whole = f"{n.denominator_value:,.0f}".replace(",", " ")
            unit = f"{n.unit} (на {whole} {n.denominator})"
    # Разрез — часть определения числа: без него число «с фильтром контура» неотличимо от числа «всего» (17.09.2026).
    head = f"{n.metric} · {n.scope}" + (f" · {n.segment}" if n.segment else "")
    return (f"{head} · {n.period_start:%d.%m.%Y}–{n.period_end:%d.%m.%Y} (правая граница исключающая)\n"
            f"  значение: {value} {unit} · статус: {n.status.value} · источник: {n.source} · снято {n.as_of:%d.%m.%Y}"
            + (f"\n  чего не хватает: {n.missing}" if n.missing else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ядро данных — ask (только чтение)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("metric")
    m.add_argument("name")
    m.add_argument("--scope", required=True)
    m.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    m.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    m.add_argument("--as-of", dest="as_of", default=None, type=date.fromisoformat)
    # Разрез числа: «contour=<набор>» для сделок, «channel_group=<группа>» для конверсии, «visit=linked» для звонков.
    # Без него из командной строки было доступно только «сколько всего» (17.09.2026).
    m.add_argument("--segment", default="", help="разрез числа, например contour=digital или channel_group=seo")
    s = sub.add_parser("sql")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=100)
    a = ap.parse_args(argv)
    cfg = load_config(Path(a.config) if a.config else Path(__file__).resolve().parents[3] / "Ядро данных" / "Конфигурация инстанса.yaml")
    url = storage_url(cfg, a.db)
    try:
        if a.cmd == "sql":
            for row in run_sql(url, a.query, a.limit):
                print(row)
            return 0
        fn = REGISTRY.get(a.name)
        if fn is None:
            print(f"метрики «{a.name}» в реестре нет; есть: {', '.join(REGISTRY)}")
            return 1
        engine = connect(url, read_only=True)
        try:
            kwargs = {"segment": a.segment} if a.segment else {}
            print(render(fn(engine, cfg, a.scope, a.start, a.end, a.as_of, **kwargs)))
        finally:
            engine.close()
        return 0
    except RuleViolation as e:
        print(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
