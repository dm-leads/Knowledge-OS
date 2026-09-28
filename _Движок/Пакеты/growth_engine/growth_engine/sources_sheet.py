"""Выгрузка карты источников на лист «Карта источников» (этап 5, К7).

Правда карты — файл `Карта источников.yaml`; лист — её снимок с датой выгрузки в `updated_at`. Перед записью имена
переменных секретов карты сверяются с именами файла секретов (страж 12): значения секретов в вывод не попадают. Новые
источники дописываются, изменённые обновляются со сверкой ревизии, строки не удаляются — источники листа, которых нет в
карте, перечисляются в отчёте. `--dry-run` печатает таблицу и ничего не пишет; `--out` — папка markdown; `--lark-book` —
книга Lark, только после «да» владельца книги. Любой страж — код возврата 1.

Запуск из Скрипты/:
  py -3 -m growth_engine.sources_sheet --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --source-map "../Планирование/Движок роста/Карта источников.yaml"
     --secrets "C:/Users/redmi/Second Brain Secrets/.env" --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from .core.artifacts import check_secret_names
from .core.config import load_config
from .core.errors import GuardViolation
from .core.source_map import load_source_map
from .core.storage import check_rows_pii, source_row
from .storage.selection import add_store_arguments, open_store


_ENV_NAME_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def env_names(path) -> tuple[str, ...]:
    """Имена переменных файла секретов: из строки берётся только имя до «=», значения не разбираются и не хранятся."""
    if not Path(path).is_file():
        raise GuardViolation(12, "файла секретов нет — имена переменных карты не сверить", GuardViolation.COVERAGE)
    with Path(path).open(encoding="utf-8-sig") as handle:
        return tuple(match.group(1) for match in map(_ENV_NAME_LINE.match, handle) if match)


def run(args, out=print, today=date.today, bridge_factory=None) -> int:
    try:
        return _run(args, out, today, bridge_factory)
    except GuardViolation as exc:
        out(f"❌ карта источников не выгружена: {exc}")
        return 1


def _run(args, out, today, bridge_factory) -> int:
    cfg = load_config(args.config)
    entries = load_source_map(args.source_map)
    check_secret_names(entries, env_names(args.secrets))
    # Страж 12 — до печати таблицы и до обращения к хранилищу.
    check_rows_pii("sources", [source_row(entry) for entry in entries], cfg.storage_link_domains)
    out(f"Карта источников: источников {len(entries)}; имена переменных секретов есть в файле секретов")
    out("| Источник | Класс | Статус | История с | Точка истины |")
    out("|---|---|---|---|---|")
    for entry in entries:
        out(f"| {entry.name} | {entry.source_class} | {entry.status} | {entry.history_from or '—'} | "
            f"{entry.truth_point or '—'} |")
    if getattr(args, "dry_run", False):
        out("сухой прогон: ничего не записано")
        return 0
    with open_store(args, cfg.storage_link_domains, today, bridge_factory) as (store, label):
        report, absent = store.sync_sources(entries)
        stored = {entry.name: entry for entry in store.read("sources")}
    out(f"хранилище: {label}")
    out(f"«{report.artifact}»: записано {report.rows_written}, прочитано {report.rows_read_back}")
    if absent:
        out(f"на листе есть источники, которых нет в карте (строки не удаляются): {', '.join(absent)}")
    same = all(stored.get(entry.name) == entry for entry in entries)
    out(f"ИТОГ: {'лист совпадает с картой' if same else 'лист НЕ совпадает с картой'} — источников в карте "
        f"{len(entries)}, на листе {len(stored)}")
    return 0 if same else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Выгрузка карты источников на лист хранилища Движка роста")
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-map", required=True)
    parser.add_argument("--secrets", required=True, help="файл секретов: сверяются только имена переменных")
    add_store_arguments(parser, dry_run=True)
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
