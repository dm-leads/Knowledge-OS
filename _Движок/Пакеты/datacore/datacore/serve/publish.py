"""Публикация чисел ядра наружу: файл, который читают потребители без доступа к базе.

Зачем файл, а не прямое чтение базы. Потребитель (движок роста) живёт своим окружением и своими зависимостями;
тянуть в него DuckDB, dlt и SQLMesh ради нескольких чисел значит склеить две системы в одну. Ядро вместо этого
**публикует** числа тем же контрактом, что отдаёт команде `ask`: период, источник, статус, дата съёма, пояснение.

Файл — снимок на дату съёма, а не живая связь. Это осознанно: число, подписанное датой, нельзя незаметно
пересчитать задним числом, а потребитель всегда видит, насколько оно свежее (П3 движка, К2 ядра).

⛔ Публикуются только агрегаты. Ни одной строки фактов, ни одного идентификатора клиента файл не содержит: он
уходит в другую систему и может попасть в git (К6).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from datacore.schema.config import load_config
from datacore.schema.engine import connect
from datacore.serve.storage import storage_url
from datacore.schema.errors import RuleViolation
from datacore.schema.number import Number
from datacore.serve.metrics import REGISTRY, last_as_of

# Метрики, которые ядро публикует по умолчанию. Список именно здесь, а не в конфигурации инстанса: это решение
# ядра о том, что оно готово отдавать наружу, а не настройка одного инстанса.
DEFAULT_METRICS = ("new_first_sql", "mql", "visits", "calls", "revenue", "payments", "paid_deals_crm",
                   "cogs", "gross_profit", "ampu", "lead_conversion", "revenue_leads", "cogs_leads",
                   "gross_profit_leads", "ampu_per_lead", "source_known", "no_trace")


# Разрезы, которые метрика понимает. Разрез отдаётся только им: иначе каждое окно каждой метрики без разреза дало бы
# строку «нет данных» (у визитов и денег разреза набора нет) и тысячи строк шума спрятали бы настоящие отказы.
SEGMENT_SUPPORT = {"contour=": ("new_first_sql",)}

# Метрики по сделкам считаются по ТЕКУЩЕМУ статусу сделки: свежая сделка ещё в работе и не стала MQL или New First,
# поэтому число свежего окна занижено и растёт задним числом. Окно, которое кончилось меньше mature_days назад, —
# «оценка» с причиной. Деньги и визиты дозревают с загрузкой, их статус ставит сама метрика.
MATURING = ("mql", "new_first_sql", "new_first_sql_by_channel", "lead_conversion", "revenue_leads", "cogs_leads",
            "gross_profit_leads", "ampu_per_lead")


def _supports(metric: str, segment: str) -> bool:
    if not segment:
        return True
    return any(segment.startswith(prefix) and metric in names for prefix, names in SEGMENT_SUPPORT.items())


def _number_to_row(n: Number) -> dict:
    """Число → строка публикации. Поля те же, что у контракта: ничего не теряется и не добавляется."""
    row = asdict(n)
    row["status"] = n.status.value
    for key in ("period_start", "period_end", "as_of"):
        row[key] = row[key].isoformat() if row[key] else None
    return row


def collect(engine, cfg, periods, metrics=DEFAULT_METRICS, scopes=None, segments=None) -> list[dict]:
    """Числа ядра за периоды. Метрика, которая для кабинета не считается, пропускается с пояснением, а не роняет
    публикацию: у кабинета может не быть своего сайта или своих денег."""
    scopes = list(scopes or cfg.scopes)
    out = []
    for metric in metrics:
        fn = REGISTRY.get(metric)
        if fn is None:
            continue
        for scope in scopes:
            for start, end in periods:
                for segment in (segments or [""]):
                    if not _supports(metric, segment):
                        continue
                    kwargs = {"segment": segment} if segment else {}
                    try:
                        got = fn(engine, cfg, scope, start, end, **kwargs)
                    except RuleViolation as exc:
                        # Отказ по правилу — это ответ ядра, а не сбой: он публикуется как «нет данных» с причиной,
                        # иначе потребитель решит, что число просто забыли.
                        out.append({"metric": metric, "scope": scope, "segment": segment,
                                    "period_start": start.isoformat(), "period_end": end.isoformat(),
                                    "value": None, "status": "нет данных", "missing": str(exc),
                                    "source": f"D:datacore:{metric}", "unit": "шт", "level": "", "flow": "all",
                                    "as_of": None, "denominator": None, "denominator_value": None})
                        continue
                    out.append(_number_to_row(got))
    return out


def days_back(today: date, count: int) -> list[tuple[date, date]]:
    """Последние закрытые дни: вчера и раньше. Сегодня не входит — день не закрыт."""
    return [(today - timedelta(days=i), today - timedelta(days=i - 1)) for i in range(count, 0, -1)]


def weeks_back(today: date, count: int) -> list[tuple[date, date]]:
    """Последние закрытые недели понедельник–воскресенье. Текущая неделя не входит, пока не кончилось воскресенье."""
    this_monday = today - timedelta(days=today.weekday())
    return [(this_monday - timedelta(weeks=i), this_monday - timedelta(weeks=i - 1)) for i in range(count, 0, -1)]


def month_to_date(today: date) -> list[tuple[date, date]]:
    """Текущий месяц нарастающим итогом по вчера включительно. Первого числа окна нет — в нём ни одного закрытого дня."""
    first = today.replace(day=1)
    return [(first, today)] if today > first else []


def months_back(today: date, count: int) -> list[tuple[date, date]]:
    """Полные месяцы назад от текущего: правая граница исключающая."""
    out = []
    year, month = today.year, today.month
    for _ in range(count):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        out.append((date(year, month, 1), end))
    return list(reversed(out))


def _downgrade(row: dict, reason: str) -> None:
    """Факт → оценка с причиной; «нет данных» и уже оценка не меняются, причина дописывается к прежней."""
    if row["value"] is None:
        return
    if row["status"] == "факт":
        row["status"] = "оценка"
    row["missing"] = "; ".join(x for x in (row.get("missing"), reason) if x)


def mark_windows(rows: list[dict], today: date, mature_days: int, open_windows=()) -> None:
    """Незакрытый месяц и недозревшие окна — «оценка» с причиной (контракт строки не меняется). open_windows — окна,
    заказанные как «месяц нарастающим итогом»: по датам их не узнать, закрытая неделя с 1-го понедельника по 8-е
    совпадает с ними (ревью 13.11.2026), поэтому вид окна передаётся явно."""
    young = today - timedelta(days=mature_days)
    open_windows = {(a.isoformat(), b.isoformat()) for a, b in open_windows}
    for row in rows:
        end = date.fromisoformat(row["period_end"])
        if (row["period_start"], row["period_end"]) in open_windows:
            _downgrade(row, f"месяц не закрыт: окно по {end - timedelta(days=1):%d.%m.%Y} включительно")
        if row["metric"] in MATURING and end > young:
            _downgrade(row, f"окно моложе {mature_days} дн.: сделки ещё в работе, число дорастёт задним числом")


def periods_for(today: date, *, months: int = 0, days: int = 0, weeks: int = 0, mtd: bool = False) -> list[tuple[date, date]]:
    """Все окна публикации без повторов, по порядку: месяцы, недели, дни, текущий месяц."""
    out = []
    for p in months_back(today, months) + weeks_back(today, weeks) + days_back(today, days) + (month_to_date(today) if mtd else []):
        if p not in out:
            out.append(p)
    return out


def publish(cfg, storage_url: str, out_path: Path, *, months: int = 6, metrics=DEFAULT_METRICS,
            scopes=None, segments=None, days: int = 0, weeks: int = 0, mtd: bool = False, mature_days: int = 7,
            today: date | None = None) -> dict:
    """Собрать числа и записать файл. Возвращает сводку: сколько чисел записано, сколько прочитано обратно, сколько
    без данных. Файл пишется во временный и переименовывается: читатель не застанет половину файла."""
    # «Сегодня» — по поясу инстанса, как и границы окон в метриках: по часам сервера в UTC до 03:00 МСК вчерашний
    # день считался бы ещё не закрытым (ревью 13.11.2026).
    today = today or datetime.now(ZoneInfo(cfg.timezone)).date()
    periods = periods_for(today, months=months, days=days, weeks=weeks, mtd=mtd)
    closed = set(months_back(today, months) + weeks_back(today, weeks) + days_back(today, days))
    open_windows = [w for w in (month_to_date(today) if mtd else []) if w not in closed]
    with connect(storage_url, read_only=True) as engine:
        rows = collect(engine, cfg, periods, metrics, scopes, segments)
        state = last_as_of(engine, (cfg.money["system"],)) if cfg.money else None
    mark_windows(rows, today, mature_days, open_windows)
    payload = {
        "instance": cfg.instance,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "as_of": state.isoformat() if state else None,
        "contract": "период с исключающей правой границей; источник «класс:система:запрос»; "
                    "статус факт/оценка/proxy/нет данных; доля обязана иметь знаменатель",
        "numbers": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    back = len(json.loads(tmp.read_text(encoding="utf-8"))["numbers"])
    no_data = sum(1 for r in rows if r["value"] is None)
    if not rows or back != len(rows):
        # Пустой или недописанный файл не заменяет прошлую публикацию: потребитель читает файл каждое утро, и вчерашние
        # честные числа лучше сегодняшнего пустого списка (ревью 13.11.2026).
        tmp.unlink(missing_ok=True)
        return {"чисел": len(rows), "прочитано обратно": back, "из них без данных": no_data,
                "файл": f"{out_path} не заменён — прошлая публикация осталась"}
    tmp.replace(out_path)
    return {"чисел": len(rows), "прочитано обратно": back, "из них без данных": no_data, "файл": str(out_path)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Публикация чисел ядра наружу (только чтение)")
    ap.add_argument("--out", required=True, help="куда записать файл публикации")
    ap.add_argument("--months", type=int, default=6, help="сколько полных месяцев назад")
    ap.add_argument("--days", type=int, default=0, help="сколько последних закрытых дней")
    ap.add_argument("--weeks", type=int, default=0, help="сколько последних закрытых недель пн–вс")
    ap.add_argument("--mtd", action="store_true", help="текущий месяц нарастающим итогом по вчера")
    ap.add_argument("--mature-days", type=int, default=7, help="окно моложе стольких дней у метрик сделок — оценка")
    ap.add_argument("--metrics", default=None, help="метрики через запятую; по умолчанию все публикуемые")
    ap.add_argument("--scopes", default=None, help="кабинеты через запятую")
    ap.add_argument("--segments", default=None, help="разрезы через запятую, например contour=digital")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    cfg = load_config(Path(a.config) if a.config else
                      Path(__file__).resolve().parents[3] / "Ядро данных" / "Конфигурация инстанса.yaml")
    report = publish(cfg, storage_url(cfg, a.db), Path(a.out), months=a.months,
                     metrics=tuple(a.metrics.split(",")) if a.metrics else DEFAULT_METRICS,
                     scopes=a.scopes.split(",") if a.scopes else None,
                     segments=a.segments.split(",") if a.segments else None,
                     days=a.days, weeks=a.weeks, mtd=a.mtd, mature_days=a.mature_days)
    for key, value in report.items():
        print(f"  {key}: {value}")
    # Успех — число записанных и прочитанных обратно строк: ноль или расхождение — сбой, а не «готово»
    return 0 if report["чисел"] and report["чисел"] == report["прочитано обратно"] else 1


if __name__ == "__main__":
    sys.exit(main())
