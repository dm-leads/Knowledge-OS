"""Числа снимка модели из хранилища (этап 7, задача 7.1): только чтение, ничего не пересчитывает.

Показывает числа последнего съёма денежной системы (`economy.money_system`) — того самого снимка, который проверяет
гейт, — строками стандарта ответа (`render()`: значение · период · знаменатель · источник · снято · статус). Числа,
построенные моделью цикла (цели и потолки дерева, эффекты гипотез — система «модель»), и числа других систем в снимок не
входят. `--all-takes` показывает все съёмы, `--metric`, `--scope`, `--flow`, `--segment` сужают выборку. Хранилище —
из конфигурации инстанса или папка markdown (`--out`); команда ничего не пишет. Пустая выборка — код 1.

Запуск из Скрипты/:
  py -3 -m growth_engine.snapshot --config "../Планирование/Движок роста/Конфигурация инстанса.yaml" --metric ampu
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from .core.arithmetic import render
from .core.config import parse_config
from .core.errors import GuardViolation
from .core.registry import MODEL_SYSTEM
from .storage.selection import add_store_arguments, open_store, store_target

FILTERS = ("metric", "scope", "flow", "segment")


def select(numbers, money_system: str, all_takes: bool, filters: dict) -> list:
    """Числа денежной системы, по умолчанию — последнего съёма; фильтры сравниваются на равенство."""
    chosen = [number for number in numbers if number.source_system == money_system and number.as_of is not None]
    if not all_takes and chosen:
        last = max(number.as_of for number in chosen)
        chosen = [number for number in chosen if number.as_of == last]
    for field, value in filters.items():
        if value is not None:
            chosen = [number for number in chosen if getattr(number, field) == value]
    return sorted(chosen, key=lambda x: (x.as_of, x.metric, x.scope, x.flow, x.segment, x.period_start))


def run(args, out=print, today=date.today, bridge_factory=None) -> int:
    try:
        return _run(args, out, today, bridge_factory)
    except GuardViolation as exc:
        out(f"❌ снимок не показан: {exc}")
        return 1


def _run(args, out, today, bridge_factory) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    money_system = (raw.get("economy") or {}).get("money_system")
    if not money_system:
        raise GuardViolation(9, "в разделе economy конфигурации не указана денежная система (money_system)")
    if money_system == MODEL_SYSTEM:
        raise GuardViolation(9, f"денежная система не может быть «{MODEL_SYSTEM}»: это числа, построенные моделью цикла")
    filters = {field: getattr(args, field, None) for field in FILTERS}
    with open_store(store_target(args, raw), cfg.storage_link_domains, today, bridge_factory) as (store, label):
        numbers = store.read("numbers")
    chosen = select(numbers, str(money_system), bool(getattr(args, "all_takes", False)), filters)
    named = ", ".join(f"{field} = {value}" for field, value in filters.items() if value is not None) or "без фильтров"
    out(f"Снимок модели: хранилище {label}; система «{money_system}»; {named}; "
        f"{'все съёмы' if getattr(args, 'all_takes', False) else 'последний съём'}")
    for number in chosen:
        out("   " + render(number))
    if not chosen:
        raise GuardViolation(13, f"в хранилище нет чисел системы «{money_system}» по этой выборке — показывать нечего",
                             GuardViolation.COVERAGE)
    takes = ", ".join(sorted({f"{number.as_of:%d.%m.%Y}" for number in chosen}))
    out(f"ИТОГ: чисел {len(chosen)}; съёмы: {takes}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Числа снимка модели из хранилища Движка роста: только чтение")
    parser.add_argument("--config", required=True)
    parser.add_argument("--metric", default=None)
    parser.add_argument("--scope", default=None, help="кабинет или сумма кабинетов, например atm+brz")
    parser.add_argument("--flow", default=None)
    parser.add_argument("--segment", default=None, help="разрез «измерение=значение»")
    parser.add_argument("--all-takes", action="store_true", help="показать все съёмы, а не последний")
    add_store_arguments(parser, required=False)
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
