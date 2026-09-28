"""Мост П2: помесячная сверка одной метрики двух систем по кабинетам — сверка, а не деление.

Мост объявляется в конфигурации по имени (`economy.bridges.<имя>`: метрика, левая и правая системы — имена секций
sources, допуск, доказательство). Так же проверяется миграция на новую систему: мост «старая — новая» по каждой
метрике денежной модели, затем переключение роли `economy.money_system`.

Калибровка (`--calibrate` или мост без допуска) пишет отчёт распределения расхождений, код возврата 0; по отчёту
допуск записывается в конфигурацию с доказательством. С допуском код возврата 1, если хоть одно окно вне допуска
(страж 2). Один прогон — один день: обе системы запрашиваются датой прогона (П3).

Запуск из Скрипты/:
  py -3 -m growth_engine.bridge --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --secrets "C:/Users/redmi/Second Brain Secrets/.env" --bridge new_first_sql_crm --start 2026-01 --months 8
     --out <файл.md> [--calibrate]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

from . import adapters as registry
from .core.windows import month_windows
from .core.config import parse_config
from .core.economy import Tolerance, reconcile, require_reconciled
from .core.errors import GuardViolation
from .sources.base import Query, SourceError


def declared_bridge(raw: dict, name: str) -> dict:
    bridges = (raw.get("economy") or {}).get("bridges") or {}
    if name not in bridges:
        raise GuardViolation(9, f"мост «{name}» не объявлен в economy.bridges конфигурации")
    spec = bridges[name]
    left, right = spec["left"], spec["right"]
    if left == right:
        raise GuardViolation(4, f"мост «{name}» соединяет систему «{left}» саму с собой")
    for system in (left, right):
        if system not in raw["sources"]:
            raise GuardViolation(9, f"система «{system}» моста «{name}» не объявлена в sources")
    left_scopes, right_scopes = set(raw["sources"][left]["scopes"]), set(raw["sources"][right]["scopes"])
    if left_scopes != right_scopes:
        raise GuardViolation(9, f"кабинеты систем моста «{name}» не совпадают: {left} {sorted(left_scopes)}, "
                                f"{right} {sorted(right_scopes)}")
    return spec


def _num(value) -> str:
    return "нет данных" if value is None else f"{value:,.0f}".replace(",", " ")


def render_report(name, spec, results, as_of: date, tolerance: Tolerance | None) -> str:
    mode = (f"допуск из конфигурации: {tolerance.abs_units:g} или {tolerance.rel:.1%}".replace(".", ",")
            if tolerance else "калибровка — допуск не применялся")
    left, right = spec["left"], spec["right"]
    lines = [f"# Мост «{name}»: {spec['metric']} — {left} против {right} — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Дата съёма обеих систем: {as_of:%d.%m.%Y}. Режим: {mode}. Окна — календарные месяцы, граница исключающая.",
             "", f"| Месяц | Кабинет | {left} | {right} | Расхождение | Доля | Сверка | Оговорки {right} |",
             "|---|---|---:|---:|---:|---:|---|---|"]
    for r in results:
        share = "—" if r.rel is None else f"{r.rel:.1%}".replace(".", ",")
        diff = "—" if r.diff is None else f"{r.diff:+,.0f}".replace(",", " ")
        verdict = "✅" if r.ok else ("нет данных" if r.diff is None else "❌")
        lines.append(f"| {r.left.period_start:%Y-%m} | {r.left.scope} | {_num(r.left.value)} | {_num(r.right.value)} | "
                     f"{diff} | {share} | {verdict} | {r.right.missing or '—'} |")
    lines += ["", f"| Кабинет | Окон | Макс. расхождение | Макс. доля | Окон «{right} больше» | Окон «{right} меньше» |",
              "|---|---:|---:|---:|---:|---:|"]
    for scope in sorted({r.left.scope for r in results}):
        rows = [r for r in results if r.left.scope == scope and r.diff is not None]
        if not rows:
            lines.append(f"| {scope} | 0 | — | — | — | — |")
            continue
        top_abs, top_rel = max(abs(r.diff) for r in rows), max(r.rel for r in rows)
        lines.append(f"| {scope} | {len(rows)} | {top_abs:,.0f} | {top_rel:.1%} | {sum(r.diff > 0 for r in rows)} | "
                     f"{sum(r.diff < 0 for r in rows)} |".replace(",", " ").replace(".", ","))
    return "\n".join(lines) + "\n"


def run(args, out=print, adapters=None, today=date.today) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    spec = declared_bridge(raw, args.bridge)
    systems = adapters or {name: registry.build(name, raw, cfg, args.secrets) for name in (spec["left"], spec["right"])}
    calibrating = args.calibrate or "abs_units" not in spec
    tolerance = None if calibrating else Tolerance(spec["abs_units"], spec["rel"])
    as_of = today()
    results = []
    for start, end in month_windows(args.start, args.months):
        for scope in raw["sources"][spec["left"]]["scopes"]:
            query = Query(metric=spec["metric"], scope=scope, flow="all", period_start=start, period_end=end,
                          breakdown=None, as_of=as_of)
            results.append(reconcile(systems[spec["left"]].fetch(query)[0], systems[spec["right"]].fetch(query)[0],
                                     tolerance or Tolerance(0, 0), spec.get("max_fetch_gap_days", 0)))
    Path(args.out).write_text(render_report(args.bridge, spec, results, as_of, tolerance), encoding="utf-8")
    outside = [r for r in results if not r.ok]
    out(f"строк отчёта: {len(results)}; расходится: {len(outside)}; отчёт: {args.out}")
    if tolerance is None:
        return 0
    try:
        require_reconciled(results)
    except GuardViolation as exc:
        out(f"❌ {exc}")
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Помесячная сверка одной метрики двух систем (мост П2)")
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--bridge", required=True, help="имя моста из economy.bridges")
    parser.add_argument("--start", required=True, help="первый месяц YYYY-MM")
    parser.add_argument("--months", type=int, required=True)
    parser.add_argument("--out", required=True, help="файл отчёта .md")
    parser.add_argument("--calibrate", action="store_true", help="не применять допуск: отчёт распределения расхождений")
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (GuardViolation, SourceError) as exc:
        print(f"❌ сверка остановлена: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
