"""Снятие чисел одного источника на лист «Модель — снимки»: метрики, окна, разрез и отбор значений разреза.

Зачем (29.09.2026): адаптеры умели разрез по страницам входа (веб-аналитика) и по кампаниям (кабинет рекламы), но ни
одна команда их не звала — числа по страницам гипотеза могла получить только вписанными руками, мимо стражей 1 и 4.
Команда общая: система — имя секции `sources` конфигурации, метрики — из `metrics`, поставщик в коде не называется.

Разрез без отбора запрещён: у сайта тысячи страниц, и лист снимков вырос бы на десятки тысяч строк. Отбор — образцы
`fnmatch` по значению разреза (`--match "/blog/*-2025-goda*"`); все значения — только явным `--match "*"`. Итог метрики
без разреза пишется всегда: доля страниц считается от него. Одна дата съёма на прогон. Успех — «записано N, прочитано N».

Запуск из Скрипты/:
  py -3 -m growth_engine.source_run --config <конфигурация> --secrets <файл секретов> --system <секция sources>
     --metrics site_visits,goal_lead_form --scope brz --flow web --months 2025-08,2026-08
     --breakdown landing_page --match "/blog/*-2025-goda*" --google-book <ключ книги>
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from . import adapters as registry
from .core.arithmetic import render
from .core.config import parse_config
from .core.errors import GuardViolation
from .core.storage import find_pii
from .funnel_run import parse_months
from .sources.base import Query, SourceError
from .storage.selection import add_store_arguments, open_store


def pick(numbers, patterns) -> list:
    """Значения разреза, подошедшие хотя бы под один образец."""
    return [x for x in numbers if any(fnmatchcase(x.segment.partition("=")[2], p) for p in patterns)]


def collect(adapter, metrics, scope: str, flow: str, windows, breakdown, patterns, as_of: date, out,
            domains=(), rules=None) -> list:
    numbers = []
    for start, end in windows:
        for metric in metrics:
            query = dict(metric=metric, scope=scope, flow=flow, period_start=start, period_end=end, as_of=as_of)
            if rules is not None and not rules.rule(metric).summable:
                # Средняя (позиция, доля) не складывается — итога у неё нет, есть только разрез (1.3.1).
                out(f"{start:%m.%Y} · {metric}: несуммируемая — итог не снимается, только разрез")
            else:
                total = adapter.fetch(Query(**query, breakdown=None))[0]
                numbers.append(total)
                out(f"{start:%m.%Y} · {render(total)}")
            if breakdown:
                parts = pick(adapter.fetch(Query(**query, breakdown=breakdown)), patterns)
                # Значение разреза, похожее на секрет или ПДн (мусор, приклеенный к адресу), хранить нельзя (страж 12):
                # оно исключается с названным числом, а не останавливает весь прогон и не пропадает молча (1.2.4).
                clean = [x for x in parts if not find_pii(x.segment, domains)]
                numbers += clean
                dropped = len(parts) - len(clean)
                out(f"   разрез «{breakdown}»: значений по отбору {len(parts)}"
                    + (f"; исключено похожих на секрет или ПДн: {dropped} (страж 12, значения не печатаются)" if dropped else ""))
    return numbers


def run(args, out=print, adapter=None, today=date.today, bridge_factory=None, google_factory=None) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    if args.system not in (raw.get("sources") or {}):
        raise GuardViolation(9, f"система «{args.system}» не объявлена в sources: {', '.join(raw.get('sources') or {})}")
    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    unknown = [m for m in metrics if m not in (raw.get("metrics") or {})]
    if not metrics or unknown:
        raise GuardViolation(9, f"метрики не объявлены в metrics конфигурации: {', '.join(unknown) or '—'}")
    patterns = list(args.match or [])
    if args.breakdown and not patterns:
        raise GuardViolation(9, "разрез без отбора значений не снимается — лист снимков вырос бы на тысячи строк; "
                                "нужен --match (все значения — только явным --match \"*\")")
    if patterns and not args.breakdown:
        raise GuardViolation(9, "--match отбирает значения разреза — без --breakdown ему нечего отбирать")
    windows = parse_months(args.months)
    if adapter is None:
        adapter = registry.build(args.system, raw, cfg, args.secrets)
    as_of = today()
    out(f"Снятие: система «{args.system}», метрик {len(metrics)}, месяцев {len(windows)}, кабинет {args.scope}, "
        f"поток {args.flow}" + (f", разрез «{args.breakdown}», образцов {len(patterns)}" if args.breakdown else "")
        + f", дата съёма {as_of:%d.%m.%Y}")
    numbers = collect(adapter, metrics, args.scope, args.flow, windows, args.breakdown, patterns, as_of, out,
                      cfg.storage_link_domains, cfg)
    if getattr(args, "dry_run", False):
        out(f"сухой прогон: чисел к записи {len(numbers)}, ничего не записано")
        return 0
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
    parser = argparse.ArgumentParser(prog="growth_engine.source_run", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--system", required=True, help="секция sources конфигурации")
    parser.add_argument("--metrics", required=True, help="метрики через запятую, из metrics конфигурации")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--flow", required=True)
    parser.add_argument("--months", required=True, help="месяцы через запятую: 2025-08,2026-08")
    parser.add_argument("--breakdown", default=None, help="разрез, который умеет адаптер системы")
    parser.add_argument("--match", action="append", help="образец значения разреза (fnmatch); можно несколько")
    add_store_arguments(parser, dry_run=True)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (GuardViolation, SourceError, NotImplementedError) as exc:
        print(f"❌ снятие остановлено: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
