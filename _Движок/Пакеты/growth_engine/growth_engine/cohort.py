"""Когорта людей и цикл сделки (П2): калибровка горизонта когорты по данным системы когорты.

Система — роль `economy.cohort_system` конфигурации. Для каждого месяца-когорты и кабинета: контакты с первым флагом
New First, покупатели и C1 когорты на каждом горизонте из `--horizons` (доля одной системы, со статусом зрелости).
Цикл сделки — перцентили дней от New First до первой оплаты на когортах, зрелых для самого длинного горизонта.
Проверка качества — доля оплат, чей контакт получил флаг New First не позже оплаты.
Отчёт markdown, код возврата 0. Горизонт выбирается по отчёту и записывается в `economy.cohort_horizon_days`
с доказательством. Один прогон — один день: система когорты синхронизирована в день прогона (П3).

Запуск из Скрипты/:
  py -3 -m growth_engine.cohort --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --secrets "C:/Users/redmi/Second Brain Secrets/.env" --start 2025-01 --months 20
     --horizons 30,60,90,180,365 --out <файл.md>
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from . import adapters as registry
from .core.windows import month_windows
from .core.arithmetic import ratio
from .core.config import parse_config
from .core.errors import GuardViolation
from .sources.base import Query, SourceError


def percentile(values: list[int], share: float) -> int | None:
    """Перцентиль ближайшего ранга: значение, не меньше которого доля share наблюдений."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def _percent(number) -> str:
    return "нет данных" if number.value is None else f"{number.value:.1%}".replace(".", ",")


def _cell(buyers, share) -> str:
    mark = "⏳ " if "дозревает" in buyers.missing else ""
    return f"{mark}{buyers.value:g} · {_percent(share)}"


def run(args, out=print, adapter=None, today=date.today) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    system = (raw.get("economy") or {}).get("cohort_system")
    if not system:
        raise GuardViolation(9, "роль economy.cohort_system не объявлена в конфигурации")
    if adapter is None:
        adapter = registry.build(system, raw, cfg, args.secrets)
    try:
        horizons = sorted({int(h) for h in args.horizons.split(",")})
    except ValueError:
        raise GuardViolation(9, f"горизонты «{args.horizons}» — целые дни через запятую") from None
    if horizons[0] <= 0:
        raise GuardViolation(9, f"горизонты «{args.horizons}» — целые дни больше нуля")
    longest, as_of = horizons[-1], today()
    windows = month_windows(args.start, args.months)
    scopes = list(raw["sources"][system]["scopes"])

    def query(metric, scope, start, end):
        return Query(metric=metric, scope=scope, flow="all", period_start=start, period_end=end, breakdown=None,
                     as_of=as_of)

    cohorts, links = [], []
    for start, end in windows:
        for scope in scopes:
            users = adapter.fetch(query("cohort_users", scope, start, end))[0]
            per_horizon = []
            for horizon in horizons:
                buyers = adapter.cohort_buyers(query("cohort_buyers", scope, start, end), horizon_days=horizon)
                per_horizon.append((buyers, ratio(buyers, users, f"c1_cohort_{horizon}d", cfg)))
            cohorts.append((start, scope, users, per_horizon))
            linked = adapter.fetch(query("payments_with_new_first", scope, start, end))[0]
            total = adapter.fetch(query("payments", scope, start, end))[0]
            links.append((start, scope, linked, total, ratio(linked, total, "payments_linked_share", cfg)))
    mature = [(start, end) for start, end in windows if end + timedelta(days=longest) <= as_of]
    cycles = {scope: [] for scope in scopes}
    for start, end in mature:
        for scope in scopes:
            cycles[scope] += adapter.cycle_days(scope, start, end, as_of=as_of, max_days=longest)

    lines = [f"# Когорта людей и цикл сделки — {system} — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Дата съёма: {as_of:%d.%m.%Y}. Когорта — контакты, чей первый флаг New First создан в месяце; покупатель "
             "когорты — оплата не раньше New First и не позже горизонта. ⏳ — когорта моложе горизонта (`оценка`).", "",
             "## Когорты", "",
             "| Месяц | Кабинет | Контакты New First | " + " | ".join(f"Покупатели · C1 за {h} дн." for h in horizons) + " |",
             "|---|---|---:|" + "---:|" * len(horizons)]
    for start, scope, users, per_horizon in cohorts:
        cells = " | ".join(_cell(buyers, share) for buyers, share in per_horizon)
        lines.append(f"| {start:%Y-%m} | {scope} | {users.value:g} | {cells} |")
    lines += ["", f"## Цикл сделки на когортах, зрелых для {longest} дн.", ""]
    if not mature:
        lines.append(f"Зрелых для {longest} дн. когорт нет: самая длинная граница ещё не прошла.")
    else:
        lines += [f"Когорты: {mature[0][0]:%Y-%m} — {mature[-1][0]:%Y-%m} ({len(mature)} мес.). Доли — среди купивших "
                  f"за {longest} дн.", "",
                  f"| Кабинет | Зрелых когорт | Покупателей за {longest} дн. | p50, дн. | p75, дн. | p90, дн. | "
                  + " | ".join(f"Купили за {h} дн." for h in horizons) + " |",
                  "|---|---:|---:|---:|---:|---:|" + "---:|" * len(horizons)]
        for scope, days in cycles.items():
            shares = " | ".join(
                (f"{sum(d < h for d in days) / len(days):.1%}".replace(".", ",") if days else "—") for h in horizons)
            p50, p75, p90 = (percentile(days, s) for s in (0.5, 0.75, 0.9))
            lines.append(f"| {scope} | {len(mature)} | {len(days)} | {p50 if p50 is not None else '—'} | "
                         f"{p75 if p75 is not None else '—'} | {p90 if p90 is not None else '—'} | {shares} |")
    lines += ["", "## Связь оплат с New First (проверка качества)", "",
              "| Месяц | Кабинет | Оплат | С флагом New First не позже оплаты | Доля |", "|---|---|---:|---:|---:|"]
    for start, scope, linked, total, share in links:
        lines.append(f"| {start:%Y-%m} | {scope} | {total.value:g} | {linked.value:g} | {_percent(share)} |")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    buyers_in_cycle = sum(len(days) for days in cycles.values())
    out(f"когорт: {len(cohorts)}; зрелых для {longest} дн.: {len(mature)} мес.; покупателей в цикле сделки: "
        f"{buyers_in_cycle}; отчёт: {args.out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Когорта людей и цикл сделки: калибровка горизонта когорты")
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--start", required=True, help="первый месяц когорты YYYY-MM")
    parser.add_argument("--months", type=int, required=True)
    parser.add_argument("--horizons", required=True, help="горизонты в днях через запятую, например 30,60,90,180,365")
    parser.add_argument("--out", required=True, help="файл отчёта .md")
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (GuardViolation, SourceError) as exc:
        print(f"❌ калибровка остановлена: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
