"""Общее для тестов пакета: синтетический инстанс и запись журнала окон денег.

Загрузчики систем-источников живут в инстансе проекта, а тестам метрик нужен журнал окон — доказательство покрытия
периода. Здесь — та же запись, что делает загрузчик денег: окно по границам запроса в поясе инстанса.
"""
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SYNTHETIC = Path(__file__).resolve().parent / "fixtures" / "instance.yaml"
WINDOW_COLUMNS = ("source_system", "window_start", "window_end", "rows_loaded", "min_started_at", "max_started_at",
                  "load_id", "finished_at")


def cogs_system(cfg) -> str:
    """Своё имя окна себестоимости: её покрытие доказывается отдельно от денег (стандарт, раздел 4а)."""
    return f"{cfg.money['system']}-cogs"


def log_money_window(engine, cfg, since: date, as_of: date, rows: list[dict], load_id: str,
                     system: str | None = None) -> None:
    """Окно денег в журнале окон: границы запроса (с `since` по день съёма включительно) в поясе инстанса."""
    system = system or cfg.money["system"]
    times = [t for t in (r.get("paid_at") or r.get("shipped_at") for r in rows) if t]
    z = ZoneInfo(cfg.timezone)
    start_at = datetime.combine(since, time.min, tzinfo=z)
    end_at = datetime.combine(as_of, time.max, tzinfo=z)
    engine.execute(f"INSERT INTO meta.window_log ({', '.join(WINDOW_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT (source_system, window_start) DO UPDATE SET window_end = EXCLUDED.window_end, "
                   "rows_loaded = EXCLUDED.rows_loaded, min_started_at = EXCLUDED.min_started_at, "
                   "max_started_at = EXCLUDED.max_started_at, load_id = EXCLUDED.load_id, finished_at = EXCLUDED.finished_at",
                   (system, since.toordinal(), as_of.toordinal(), len(rows),
                    min(times + [start_at]), max(times + [end_at]), load_id, datetime.now(timezone.utc)))
    engine.commit()
