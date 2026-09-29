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
from datetime import date, datetime, timezone
from pathlib import Path

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


def months_back(today: date, count: int) -> list[tuple[date, date]]:
    """Полные месяцы назад от текущего: правая граница исключающая."""
    out = []
    year, month = today.year, today.month
    for _ in range(count):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        out.append((date(year, month, 1), end))
    return list(reversed(out))


def publish(cfg, storage_url: str, out_path: Path, *, months: int = 6, metrics=DEFAULT_METRICS,
            scopes=None, segments=None) -> dict:
    """Собрать числа и записать файл. Возвращает сводку: сколько чисел, сколько без данных."""
    periods = months_back(date.today(), months)
    with connect(storage_url, read_only=True) as engine:
        rows = collect(engine, cfg, periods, metrics, scopes, segments)
        state = last_as_of(engine, (cfg.money["system"],)) if cfg.money else None
    payload = {
        "instance": cfg.instance,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "as_of": state.isoformat() if state else None,
        "contract": "период с исключающей правой границей; источник «класс:система:запрос»; "
                    "статус факт/оценка/proxy/нет данных; доля обязана иметь знаменатель",
        "numbers": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    no_data = sum(1 for r in rows if r["value"] is None)
    return {"чисел": len(rows), "из них без данных": no_data, "файл": str(out_path)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Публикация чисел ядра наружу (только чтение)")
    ap.add_argument("--out", required=True, help="куда записать файл публикации")
    ap.add_argument("--months", type=int, default=6, help="сколько полных месяцев назад")
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
                     segments=a.segments.split(",") if a.segments else None)
    for key, value in report.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
