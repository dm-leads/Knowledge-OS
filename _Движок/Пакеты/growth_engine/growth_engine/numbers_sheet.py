"""Перенос снимка чисел в хранилище (этап 5): снимок прогона модели → лист «Модель — снимки».

Источник — папка снимка: файл артефакта прежнего формата (`MarkdownStore`, прогоны этапов 2–3; ключ `--artifact`) или
markdown-хранилище контракта (лист «Модель — снимки»; без `--artifact`). Числа не пересчитываются и не меняются: номер
числа строится из его идентичности, повтор — 0 строк. Разные числа под одним номером останавливают перенос ещё до записи.
`--dry-run` печатает сводку и ничего не пишет; `--out` — папка markdown; `--lark-book` — книга Lark, только после «да»
владельца книги. Любой страж — код возврата 1.

Запуск из Скрипты/:
  py -3 -m growth_engine.numbers_sheet --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --snapshot "../Планирование/Данные/Денежная модель — прогон 2026-09-14"
     --artifact "денежная модель 2026-06–2026-08" --dry-run
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from .core.config import load_config
from .core.errors import GuardViolation
from .core.storage import SHEETS, RegistryStore, number_id
from .storage.markdown import MarkdownBackend, MarkdownStore
from .storage.selection import add_store_arguments, open_store


def read_snapshot(folder, artifact: str | None) -> list:
    """Числа снимка: файл артефакта прежнего формата или лист «Модель — снимки» markdown-хранилища контракта."""
    folder = Path(folder)
    name = artifact or SHEETS["numbers"]
    if not (folder / f"{name}.md").is_file():
        raise GuardViolation(13, f"в снимке «{folder.name}» нет файла «{name}.md»", GuardViolation.COVERAGE)
    if artifact:
        return MarkdownStore(folder).read_numbers(artifact)
    return RegistryStore(MarkdownBackend(folder)).read("numbers")


def clashes(numbers) -> list[str]:
    """Номера, под которыми в снимке разные числа: снимок записан до правила «запрос называет расчёт»."""
    variants = {}
    for number in numbers:
        known = variants.setdefault(number_id(number), [])
        if number not in known:
            known.append(number)
    return [f"{key} — {known[0].metric}, {known[0].period_start}–{known[0].period_end}, {known[0].source}: "
            f"вариантов {len(known)}" for key, known in variants.items() if len(known) > 1]


def run(args, out=print, today=date.today, bridge_factory=None) -> int:
    try:
        return _run(args, out, today, bridge_factory)
    except GuardViolation as exc:
        out(f"❌ снимок чисел не перенесён: {exc}")
        return 1


def _run(args, out, today, bridge_factory) -> int:
    cfg = load_config(args.config)
    numbers = read_snapshot(args.snapshot, getattr(args, "artifact", None))
    ids = {number_id(number) for number in numbers}
    statuses = Counter(number.status.value for number in numbers)
    out(f"Снимок «{Path(args.snapshot).name}»: чисел {len(numbers)}, разных номеров {len(ids)}; даты съёма: "
        f"{', '.join(sorted({number.as_of.isoformat() for number in numbers})) or '—'}; статусы: "
        f"{', '.join(f'{status} — {count}' for status, count in sorted(statuses.items())) or '—'}")
    found = clashes(numbers)
    if found:
        raise GuardViolation(13, f"под одним номером разные числа: {'; '.join(found)} — у разных расчётов часть "
                                 "«запрос» источника должна называть расчёт; перенос остановлен")
    if getattr(args, "dry_run", False):
        out("сухой прогон: ничего не записано")
        return 0
    with open_store(args, cfg.storage_link_domains, today, bridge_factory) as (store, label):
        report = store.create_numbers(numbers)
        stored = {number_id(number) for number in store.read("numbers")}
    missing = ids - stored
    out(f"хранилище: {label}")
    out(f"«{report.artifact}»: записано {report.rows_written}, прочитано {report.rows_read_back}")
    verdict = "все числа снимка в хранилище" if not missing else f"в хранилище нет чисел снимка: {len(missing)}"
    out(f"ИТОГ: {verdict} — номеров в снимке {len(ids)}")
    return 0 if not missing else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Перенос снимка чисел в хранилище Движка роста")
    parser.add_argument("--config", required=True)
    parser.add_argument("--snapshot", required=True, help="папка снимка прогона")
    parser.add_argument("--artifact", default=None, help="имя файла снимка прежнего формата без .md")
    add_store_arguments(parser, dry_run=True)
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
