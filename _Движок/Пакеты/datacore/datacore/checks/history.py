"""Перелом в истории (Я3): доля заполненной колонки у сделок New First по дню создания и число изменений поля в день.
Ряд из разбора 09.09.2026 (11–41 % → 50–68 %) снят на состоянии данных того дня и не заморожен; ворота — скачок."""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from datacore.serve.metrics import window

_COLUMNS = ("entry_channel_tech", "entry_channel_summary", "marker", "parent_deal_id")


def fill_rate_by_day(engine, cfg, column: str, start: date, end: date, only_new_first: bool = True):
    if column not in _COLUMNS:
        raise ValueError(f"колонка {column!r} не из списка {_COLUMNS}")
    lo, hi = window(start, end, cfg.timezone)
    cond = "AND is_new_first" if only_new_first else ""
    rows = engine.fetchall(
        f"SELECT CAST(timezone(?, created_at) AS DATE) AS d, COUNT(*), SUM(CASE WHEN {column} IS NOT NULL THEN 1 ELSE 0 END) "
        f"FROM facts.deal WHERE deleted_at IS NULL {cond} AND created_at >= ? AND created_at < ? GROUP BY d ORDER BY d",
        (cfg.timezone, lo, hi))
    return [(d, int(n), int(f)) for d, n, f in rows]


def changes_by_day(engine, field: str, start: date, end: date, tz: str = "Europe/Moscow"):
    lo, hi = window(start, end, tz)
    rows = engine.fetchall(
        "SELECT CAST(timezone(?, changed_at) AS DATE) AS d, COUNT(*) FROM facts.deal_field_change "
        "WHERE field = ? AND changed_at >= ? AND changed_at < ? GROUP BY d ORDER BY d", (tz, field, lo, hi))
    return [(d, int(n)) for d, n in rows]


@dataclass(frozen=True)
class Report:
    rate_before: float
    rate_after: float
    changes_before: float
    changes_after: float

    @property
    def ok(self) -> bool:
        """Перелом виден: доля выросла хотя бы на 15 п.п., а событий в день стало не меньше чем вдвое больше — и они есть."""
        return (self.rate_after >= self.rate_before + 0.15 and self.changes_after > 0
                and self.changes_after >= 2 * self.changes_before)


def _median(xs):
    return statistics.median(xs) if xs else 0.0


def breakpoint_report(engine, cfg, column: str, field: str, break_day: date, days: int = 14) -> Report:
    start, end = break_day - timedelta(days=days), break_day + timedelta(days=days + 1)
    rate = fill_rate_by_day(engine, cfg, column, start, end)
    before = [f / n for d, n, f in rate if d < break_day and n >= 5]
    after = [f / n for d, n, f in rate if d > break_day and n >= 5]
    ch = dict(changes_by_day(engine, field, start, end, cfg.timezone))
    cb = [ch.get(start + timedelta(days=i), 0) for i in range(days)]
    ca = [ch.get(break_day + timedelta(days=1 + i), 0) for i in range(days)]
    return Report(round(_median(before), 3), round(_median(after), 3), _median(cb), _median(ca))


def main(argv=None) -> int:
    from datacore.schema.config import load_config
    from datacore.schema.engine import connect
    from datacore.serve.storage import storage_url
    ap = argparse.ArgumentParser(description="Перелом в истории: заполняемость колонки и события по дням")
    ap.add_argument("--column", required=True)
    ap.add_argument("--field", required=True)
    ap.add_argument("--break", dest="break_day", required=True, type=date.fromisoformat)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--db", default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    cfg = load_config(Path(a.config) if a.config else Path(__file__).resolve().parents[3] / "Ядро данных" / "Конфигурация инстанса.yaml")
    # Адрес — через общий помощник: на сервере база в окружении, и своя сборка адреса читала бы локальный файл
    # вместо рабочей базы (найдено проверкой точек входа при переносе в пакет, 28.09.2026).
    engine = connect(storage_url(cfg, a.db), read_only=True)
    try:
        start, end = a.break_day - timedelta(days=a.days), a.break_day + timedelta(days=a.days + 1)
        for d, n, f in fill_rate_by_day(engine, cfg, a.column, start, end):
            print(f"{d}: сделок New First {n}, {a.column} заполнено {f} ({100 * f / n:.0f}%)")
        for d, n in changes_by_day(engine, a.field, start, end, cfg.timezone):
            print(f"{d}: изменений поля {a.field}: {n}")
        r = breakpoint_report(engine, cfg, a.column, a.field, a.break_day, a.days)
    finally:
        engine.close()
    print(f"медиана доли: до {r.rate_before:.0%} → после {r.rate_after:.0%}; медиана событий/день: {r.changes_before:g} → {r.changes_after:g}; "
          f"{'перелом виден' if r.ok else 'перелома нет'}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
