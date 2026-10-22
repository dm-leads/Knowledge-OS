"""Живой прогон денежной модели (этап 3, задача 3.7): одна система, одно окно, один съём.

Система денежной модели — роль `economy.money_system`, метрики — роли `economy.money_metrics`, разрез «канал» —
`economy.channel_breakdown`; поставщик в коде не называется. Порядок прогона:
1. мосты `economy.bridges` помесячно по кабинетам, с месяца `sum_scopes_from` — по сумме кабинетов: сверка систем
   (страж 2), до расчёта модели;
2. денежная модель каждого кабинета: C1 на оплату, средний чек, маржа, AMPPU на оплату, AMPU на New First SQL —
   с кросс-чеком «выручка − себестоимость = прибыль» (страж 2), порогом шума C1 и дозреванием окна (Т6);
3. по компании — AMPU и маржа из разрешённых сумм (оплаты кабинетов не складываются);
4. прибыль на New First SQL по каналам — `proxy`, деньги дозревают;
5. окно цели — среднее последних k месяцев и требуемое в следующем месяце под baseline конфигурации; метрика окна —
   из рамки цели (`goal.system`, `goal.scope`, `goal.segment`), если она задана, иначе — из денежной системы.
Числа пишутся на лист «Модель — снимки» через контракт хранилища (markdown) и читаются обратно. Любой страж — код возврата 1.

Запуск из Скрипты/:
  py -3 -m growth_engine.economy_run --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --secrets "C:/Users/redmi/Second Brain Secrets/.env" --start 2026-06 --months 3 --out <папка>
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import fields, replace
from datetime import date, timedelta
from pathlib import Path

import yaml

from . import adapters as registry
from .core.windows import month_windows
from .core.arithmetic import add, render
from .core.config import parse_config
from .core.economy import (Tolerance, mature, money_model, money_share, noise, per_unit, reconcile,
                           require_reconciled, required_last_month, window_average)
from .core.errors import GuardViolation
from .core.number import Status
from .sources.base import Query, SourceError
from .storage.selection import add_store_arguments, open_store

ROLES = ("users", "sales", "revenue", "cogs", "profit")


def _month_start(text: str) -> date:
    """«ГГГГ-ММ» → первое число месяца; другой вид — страж 9 с допустимой формой."""
    try:
        year, month = (int(part) for part in str(text).split("-"))
        return date(year, month, 1)
    except ValueError as exc:
        raise GuardViolation(9, f"sum_scopes_from = {text!r}: нужен месяц вида ГГГГ-ММ") from exc


def _declared(economy: dict, key: str):
    if not economy.get(key):
        raise GuardViolation(9, f"роль economy.{key} не объявлена в конфигурации")
    return economy[key]


def run(args, out=print, adapters=None, today=date.today, bridge_factory=None) -> int:
    try:
        return _run(args, out, dict(adapters or {}), today, bridge_factory)
    except (GuardViolation, SourceError) as exc:
        out(f"❌ прогон остановлен: {exc}")
        return 1


def _run(args, out, systems: dict, today, bridge_factory) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    economy = raw.get("economy") or {}
    system = _declared(economy, "money_system")
    names = _declared(economy, "money_metrics")
    absent = [role for role in ROLES if role not in names]
    if absent:
        raise GuardViolation(9, f"в economy.money_metrics нет ролей: {', '.join(absent)}")
    as_of = today()
    windows = month_windows(args.start, args.months)
    start, end = windows[0][0], windows[-1][1]
    scopes = list(raw["sources"][system]["scopes"])

    def adapter(name):
        if name not in systems:
            systems[name] = registry.build(name, raw, cfg, args.secrets)
        return systems[name]

    def fetch(name, metric, scope, period_start, period_end, breakdown=None):
        return adapter(name).fetch(Query(metric=metric, scope=scope, flow="all", period_start=period_start,
                                         period_end=period_end, breakdown=breakdown, as_of=as_of))

    last_day = end - timedelta(days=1)
    out(f"Денежная модель: система «{system}», окно {start:%d.%m.%Y}–{last_day:%d.%m.%Y}, дата съёма {as_of:%d.%m.%Y}")

    for name, spec in (economy.get("bridges") or {}).items():
        if "abs_units" not in spec:
            out(f"⏭ мост «{name}»: допуска нет — только калибровка командой bridge.py")
            continue
        tolerance = Tolerance(spec["abs_units"], spec["rel"])
        bridge_scopes = list(raw["sources"][spec["left"]]["scopes"])
        # С месяца `sum_scopes_from` системы делят кабинеты по-разному (например, одна — по кабинету учёта, другая —
        # по полю бренда), и помесячная сверка по кабинетам расходится при совпадающем итоге. С этого месяца мост
        # сверяет сумму кабинетов; раньше — каждый кабинет, как прежде. Сумма — по правилам стража 3.
        sum_from = _month_start(spec["sum_scopes_from"]) if spec.get("sum_scopes_from") else None

        def side(system, month_start, month_end, by_sum):
            parts = [fetch(system, spec["metric"], scope, month_start, month_end)[0] for scope in bridge_scopes]
            return [add(parts, cfg)] if by_sum and len(parts) > 1 else parts

        results = []
        for month_start, month_end in windows:
            by_sum = sum_from is not None and month_start >= sum_from
            results += [reconcile(left, right, tolerance, spec.get("max_fetch_gap_days", 0))
                        for left, right in zip(side(spec["left"], month_start, month_end, by_sum),
                                               side(spec["right"], month_start, month_end, by_sum))]
        require_reconciled(results)
        note = f"; с {sum_from:%m.%Y} — по сумме кабинетов" if sum_from else ""
        out(f"✅ мост «{name}»: окон {len(results)}, все в допуске{note}")

    numbers, models = [], {}
    for scope in scopes:
        model = money_model(**{role: fetch(system, names[role], scope, start, end)[0] for role in ROLES}, cfg=cfg)
        models[scope] = model
        c1_noise = noise(model.c1)
        numbers += [getattr(model, field.name) for field in fields(model)] + [c1_noise]
        out(f"Кабинет {scope}:")
        for x in (model.c1, c1_noise, model.avg_check, model.margin, model.amppu, model.ampu):
            out("   " + render(x))

    if len(scopes) > 1:
        profit, users, revenue = (add([getattr(models[scope], role) for scope in scopes], cfg)
                                  for role in ("profit", "users", "revenue"))
        company = [per_unit(profit, users, "ampu", cfg), money_share(profit, revenue, "margin", cfg)]
        numbers += company
        out("Компания:")
        for x in company:
            out("   " + render(x))

    breakdown = economy.get("channel_breakdown")
    if breakdown:
        for scope in scopes:
            profits = {x.segment: x for x in fetch(system, names["profit"], scope, start, end, breakdown)}
            users_by_segment = {x.segment: x for x in fetch(system, names["users"], scope, start, end, breakdown)}
            if not profits:
                out(f"Каналы кабинета {scope}: денег по каналам в ответе нет")
                continue
            currency = next(iter(profits.values())).unit
            channels = []
            for segment in sorted(set(profits) | set(users_by_segment)):
                money, users = profits.get(segment), users_by_segment.get(segment)
                if money is None:
                    money = replace(users, metric=names["profit"], level=cfg.rule(names["profit"]).level, unit=currency,
                                    status=Status.NO_DATA, value=None, missing="в ответе нет денег сегмента")
                if users is None:
                    users = replace(money, metric=names["users"], level=cfg.rule(names["users"]).level, unit="шт",
                                    status=Status.NO_DATA, value=None, missing="в ответе нет счётчика сегмента")
                if cfg.cohort_horizon_days:
                    money = mature(money, cfg.cohort_horizon_days)
                channels.append(per_unit(money, users, "profit_per_user", cfg))
            numbers += channels
            counted = [x for x in channels if x.value is not None]
            out(f"Каналы кабинета {scope}: посчитано {len(counted)}, нет данных {len(channels) - len(counted)} "
                "(нет New First SQL или денег сегмента)")
            for x in sorted(counted, key=lambda number: -number.value):
                out("   " + render(x))

    k = cfg.goal_window_months
    if len(windows) >= k:
        goal_months = []
        for month_start, month_end in windows[-k:]:
            if cfg.goal_system:
                # Рамка цели из конфигурации: своя система, кабинет и разрез — число берётся как есть, без сложения.
                # Адаптер с разрезом может вернуть список сегментов: берётся только число нужного разреза, ровно одно.
                got = [x for x in fetch(cfg.goal_system, cfg.goal_metric, cfg.goal_scope, month_start, month_end,
                                        cfg.goal_segment or None) if (x.segment or "") == cfg.goal_segment]
                if len(got) != 1:
                    raise GuardViolation(9, f"система цели «{cfg.goal_system}» за {month_start:%m.%Y}: чисел разреза "
                                            f"«{cfg.goal_segment or 'без разреза'}» {len(got)}, нужно ровно одно")
                goal_months.append(got[0])
                continue
            parts = [fetch(system, cfg.goal_metric, scope, month_start, month_end)[0] for scope in scopes]
            goal_months.append(add(parts, cfg) if len(parts) > 1 else parts[0])
        average = window_average(goal_months, cfg)
        need = required_last_month(goal_months[1:], cfg.goal_baseline, cfg)
        numbers += goal_months + [average, need]
        frame = (f" — система «{cfg.goal_system}», кабинет {cfg.goal_scope}"
                 + (f", разрез {cfg.goal_segment}" if cfg.goal_segment else "")) if cfg.goal_system else ""
        out(f"Окно цели{frame}:")
        for x in (average, need):
            out("   " + render(x))

    if getattr(args, "dry_run", False):
        out("сухой прогон: ничего не записано")
        out(f"ИТОГ: пройден — чисел {len(numbers)}; записано 0, прочитано 0")
        return 0
    with open_store(args, cfg.storage_link_domains, today, bridge_factory) as (store, label):
        report = store.create_numbers(numbers)
    out(f"хранилище: {label}")
    out(f"ИТОГ: пройден — чисел {len(numbers)}; записано {report.rows_written}, прочитано {report.rows_read_back}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Живой прогон денежной модели Движка роста")
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--start", required=True, help="первый месяц окна YYYY-MM")
    parser.add_argument("--months", type=int, required=True)
    add_store_arguments(parser, dry_run=True)
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
