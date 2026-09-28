"""Проверка аддитивности метрик сквозной аналитики по окнам (ловушка Т2): отчёт и сверка с конфигурацией.

Для каждой метрики и кабинета сравнивается сумма месячных окон с окном за весь период. Отчёт пишется в
markdown; код возврата 1, если флаг period_additive в конфигурации противоречит данным. Один запрос на окно
и кабинет — все метрики сразу.

Система берётся по роли `economy.money_system` и собирается реестром адаптеров (28.09.2026): команда не знает
поставщика, ей нужен только адаптер с итогами по окну (`totals`). Нет такого адаптера — стоп, а не пустой отчёт.

Запуск из Скрипты/:
  py -3 -m growth_engine.additivity --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --secrets "C:/Users/redmi/Second Brain Secrets/.env" --start 2026-06 --months 3 --out <файл.md>
  --without-settings — тот же прогон запросом без settings (так сняты снимки 03.09): разбор расхождений со снимками.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

from .adapters import build
from .core.config import parse_config
from .core.errors import GuardViolation
from .core.windows import month_windows
from .sources.base import SourceError


def check(adapter, section: dict, cfg, windows) -> list[dict]:
    metrics = list(section["metric_names"])
    money = set(section["money_metrics"])
    rows = []
    for scope in section["scopes"]:
        monthly = [adapter.totals(scope, metrics, start, end) for start, end in windows]
        whole = adapter.totals(scope, metrics, windows[0][0], windows[-1][1])
        for metric in metrics:
            months_sum = sum(values[metric] for values in monthly)
            tolerance = 1.0 if metric in money else 0.5
            additive = abs(months_sum - whole[metric]) <= tolerance
            flag = cfg.rule(metric).period_additive
            rows.append({"metric": metric, "scope": scope, "months_sum": months_sum, "period": whole[metric],
                         "additive": additive, "flag": flag, "matches": additive == flag})
    return rows


def render_report(rows, windows, settings_note: str, system: str) -> str:
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")
    period = f"{windows[0][0]:%d.%m.%Y}–{(windows[-1][1]).replace(day=1):%d.%m.%Y} (окна по месяцам: {len(windows)})"
    lines = [f"# Аддитивность метрик сквозной аналитики по окнам — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Период: {period}. Источник: система «{system}» (роль денежной модели), поток all, без фильтров, "
             f"{settings_note}.", "",
             "| Метрика | Кабинет | Сумма месяцев | Окно за период | Складывается | Флаг в конфигурации | Сверка |",
             "|---|---|---:|---:|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['metric']} | {row['scope']} | {fmt(row['months_sum'])} | {fmt(row['period'])} | "
                     f"{'да' if row['additive'] else 'нет'} | {'да' if row['flag'] else 'нет'} | "
                     f"{'✅' if row['matches'] else '❌ не совпадает'} |")
    return "\n".join(lines) + "\n"


def money_system(raw: dict) -> str:
    """Система денежной модели из роли конфигурации; роли нет или система не описана в sources — страж 9."""
    system = (raw.get("economy") or {}).get("money_system")
    if not system or system not in (raw.get("sources") or {}):
        raise GuardViolation(9, "система сквозной аналитики не задана: нужна роль economy.money_system, описанная в "
                                "sources конфигурации")
    return system


def run(args, out=print, adapter_factory=build) -> int:
    """`adapter_factory(name, raw, cfg, secrets_path)` — сборка адаптера, как у реестра адаптеров (подмена в тестах)."""
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    system = money_system(raw)
    section = raw["sources"][system]
    if args.without_settings:
        section = {**section, "settings": {}}
        settings_note = "без settings (запрос снимков 03.09)"
    else:
        settings_note = f"settings инстанса {section.get('settings', {})}"
    adapter = adapter_factory(system, {**raw, "sources": {**raw["sources"], system: section}}, cfg, args.secrets)
    if not callable(getattr(adapter, "totals", None)):
        raise GuardViolation(9, f"адаптер системы «{system}» не отдаёт итоги по окну (totals) — аддитивность не "
                                "проверяется")
    windows = month_windows(args.start, args.months)
    rows = check(adapter, section, cfg, windows)
    Path(args.out).write_text(render_report(rows, windows, settings_note, system), encoding="utf-8")
    mismatches = [row for row in rows if not row["matches"]]
    for row in mismatches:
        out(f"❌ {row['metric']} ({row['scope']}): сумма месяцев {row['months_sum']:g}, окно {row['period']:g}, "
            f"флаг {row['flag']}")
    out(f"строк отчёта: {len(rows)}; противоречий с конфигурацией: {len(mismatches)}; отчёт: {args.out}")
    return 1 if mismatches else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Аддитивность метрик по окнам против флагов конфигурации")
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--start", required=True, help="первый месяц YYYY-MM")
    parser.add_argument("--months", type=int, required=True)
    parser.add_argument("--out", required=True, help="файл отчёта .md")
    parser.add_argument("--without-settings", action="store_true",
                        help="запрос без settings, как в снимках 03.09 — для разбора расхождений со снимками")
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (GuardViolation, SourceError) as exc:
        print(f"❌ проверка остановлена: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
