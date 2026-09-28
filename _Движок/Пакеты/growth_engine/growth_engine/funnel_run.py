"""Живая воронка по каналам: ступени помесячно, по кабинетам, потокам и каналам — в лист «Модель — снимки».

Зачем (24.09.2026): воронку по каналам движок брал только из замороженных снимков приёмочной сцены, а лист «Воронка и
оценка гипотез» и гипотезы по цели требуют живых чисел. Метод — Красинский: воронка в людях, конверсия от первого шага
и «не прошли» на каждой ступени; проценты — вторичны.

Система и ступени — раздел `funnel` конфигурации, разрез «канал» — `economy.channel_breakdown`; поставщик в коде не
называется. Визиты бывают только у веб-потока. Ступени не складываются между потоками и кабинетами: каждое число
записано со своим потоком, кабинетом и каналом. Одна дата съёма на прогон. Успех — «записано N, прочитано N».

Запуск из Скрипты/:
  py -3 -m growth_engine.funnel_run --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --secrets "C:/Users/redmi/Second Brain Secrets/.env" --months 2026-05,2026-08 --google-book <ключ книги>
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

from . import adapters as registry
from .core.config import parse_config
from .core.errors import GuardViolation
from .sources.base import Query, SourceError
from .storage.selection import add_store_arguments, open_store

FLOWS = ("web", "no_visit")
FLOW_NAMES = {"web": "веб", "no_visit": "без визита"}
STAGE_NAMES = {"visits": "визиты", "leads": "заявки", "mql": "MQL", "new_first_sql": "New First SQL",
               "sales": "продажи"}
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def parse_months(text: str) -> list[tuple[date, date]]:
    """«2026-05,2026-08» → окна месяцев с исключающей правой границей."""
    months = [part.strip() for part in (text or "").split(",") if part.strip()]
    bad = [month for month in months if not _MONTH.match(month)]
    if not months or bad:
        raise GuardViolation(9, f"месяцы воронки: нужен список ГГГГ-ММ через запятую, получено «{text}»")
    windows = []
    for month in months:
        year, mon = int(month[:4]), int(month[5:])
        windows.append((date(year, mon, 1), date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)))
    return windows


def _people(value) -> str:
    return "нет данных" if value is None else f"{value:,.0f}".replace(",", " ")


def summary(month: date, scope: str, flow: str, totals: dict) -> str:
    """Строка воронки: сколько дошло на каждой ступени, конверсия от первого шага и сколько не прошли."""
    stages = list(totals)
    first = totals[stages[0]].value
    parts = [f"{STAGE_NAMES.get(stages[0], stages[0])} {_people(first)}"]
    previous = first
    for stage in stages[1:]:
        value = totals[stage].value
        if value is None or not first or previous is None:
            parts.append(f"{STAGE_NAMES.get(stage, stage)} {_people(value)}")
            previous = value
            continue
        share = f"{value / first * 100:.2f}".replace(".", ",")
        # «Не прошли» — дошли до прошлой ступени и не перешли на эту: вошло − вышло (Красинский, 1 − конверсия).
        parts.append(f"{STAGE_NAMES.get(stage, stage)} {_people(value)} ({share}% от первого шага; "
                     f"не прошли {_people(previous - value)})")
        previous = value
    return f"{month:%m.%Y} · {scope} · {FLOW_NAMES[flow]}: " + " → ".join(parts)


def run(args, out=print, adapter=None, today=date.today, bridge_factory=None,
        google_factory=None) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    funnel = raw.get("funnel") or {}
    stages, system = list(funnel.get("stages") or []), funnel.get("system")
    if len(stages) < 2 or not system:
        raise GuardViolation(9, "в конфигурации нет раздела funnel с системой и хотя бы двумя ступенями")
    unknown = [stage for stage in stages if stage not in (raw.get("metrics") or {})]
    if unknown:
        raise GuardViolation(9, f"ступени воронки не объявлены в metrics: {', '.join(unknown)}")
    breakdown = (raw.get("economy") or {}).get("channel_breakdown")
    if not breakdown:
        raise GuardViolation(9, "в конфигурации нет economy.channel_breakdown — разрез «канал» неизвестен")
    windows = parse_months(args.months)
    if adapter is None:
        adapter = registry.build(system, raw, cfg, args.secrets)
    as_of = today()   # одна дата съёма на весь прогон (П3)
    scopes = list(raw["sources"][system]["scopes"])

    numbers = []
    out(f"Воронка по каналам: система «{system}», месяцев {len(windows)}, кабинетов {len(scopes)}, "
        f"дата съёма {as_of:%d.%m.%Y}")
    for start, end in windows:
        for scope in scopes:
            for flow in FLOWS:
                totals = {}
                for stage in stages:
                    if stage == "visits" and flow != "web":
                        continue      # визитов у потока без визита нет по определению потока
                    query = dict(metric=stage, scope=scope, flow=flow, period_start=start, period_end=end,
                                 as_of=as_of)
                    total = adapter.fetch(Query(**query, breakdown=None))[0]
                    numbers += [total] + adapter.fetch(Query(**query, breakdown=breakdown))
                    totals[stage] = total
                out(summary(start, scope, flow, totals))

    with open_store(args, cfg.storage_link_domains, today=today, bridge_factory=bridge_factory,
                    google_factory=google_factory) as (store, label):
        report = store.create_numbers(numbers)
    out(f"хранилище: {label}")
    out(f"«{report.artifact}»: записано {report.rows_written}, прочитано {report.rows_read_back}")
    out(f"ИТОГ: чисел в прогоне {len(numbers)}; записано {report.rows_written}, прочитано {report.rows_read_back}")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="growth_engine.funnel_run", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--months", required=True, help="месяцы через запятую: 2026-05,2026-08")
    add_store_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (GuardViolation, SourceError) as exc:
        print(f"❌ воронка остановлена: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
