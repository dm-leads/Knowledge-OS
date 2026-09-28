"""ТЗ исполнителю в строке гипотезы: владелец копирует его из листа «Гипотезы» в профильный чат.

Зачем (24.09.2026): задачи гипотез делают профильные чаты — контент-машина, рефреш статей, Директ, сайт. ТЗ пишется по
семи правилам постановки (что сделать целиком, что значит «готово», зачем, чего не трогать и почему, когда
остановиться) и кончается требованием записать факт исполнения в файл
`Планирование/Движок роста/Исполнение/<номер гипотезы>.md` — оттуда движок берёт дату и зону запуска теста, и в чаты
за отчётом ходить не нужно.

«Исполнитель», «ТЗ исполнителю» и «Как это работает» — колонки людей: движок их не читает, ревизию строки гипотезы они
не меняют. «Как это работает» (с 24.09.2026, просьба владельца) — механика гипотезы простыми словами: ключевая идея для
пользователя, что он делает, почему это ведёт к заявке; формулировку гипотезы после заведения не меняют, конкретика — здесь.
ТЗ пишется агенту, который ГОТОВИТ материал из наших данных: сайт на конструкторе, меняет его владелец руками.
Текст проходит проверку на ПДн и секреты до записи (страж 12). Новое ТЗ заменяет старое.

Запуск из Скрипты/:
  py -3 -m growth_engine.task_brief --config <конфигурация> --id H-17 --executor "чат сайта" --brief <файл ТЗ>
     --google-book <ключ книги>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .core.config import parse_config
from .core.errors import GuardViolation
from .core.storage import SHEETS, decode_text, find_pii
from .storage.selection import add_store_arguments, open_store

EXECUTOR = "Исполнитель"
BRIEF = "ТЗ исполнителю"
HOW = "Как это работает"
BRIEF_WIDTH = 420


def run(args, out=print) -> int:
    cfg = parse_config(yaml.safe_load(Path(args.config).read_text(encoding="utf-8")))
    text = Path(args.brief).read_text(encoding="utf-8").strip() if Path(args.brief).is_file() else ""
    executor = (args.executor or "").strip()
    if not text or not executor:
        raise GuardViolation(9, "ТЗ: нужны непустой файл ТЗ (--brief) и исполнитель (--executor)")
    how_path = getattr(args, "how", None)
    how = Path(how_path).read_text(encoding="utf-8").strip() if how_path and Path(how_path).is_file() else ""
    found = find_pii(f"{executor}\n{text}\n{how}", cfg.storage_link_domains)
    if found:
        raise GuardViolation(12, f"ТЗ {args.id}: в тексте похоже на {', '.join(sorted(set(found)))} — не записано")
    sheet = SHEETS["hypotheses"]
    with open_store(args, cfg.storage_link_domains) as (store, label):
        backend = store.backend
        rows = backend.read_rows(sheet) if backend.header(sheet) is not None else []
        position = next((i for i, row in enumerate(rows) if decode_text(row.get("id", "")) == args.id), None)
        if position is None:
            raise GuardViolation(13, f"ТЗ: гипотезы {args.id} нет на листе «{sheet}» — сначала завести её командой реестра")
        wanted = {EXECUTOR: executor, BRIEF: text, **({HOW: how} if how else {})}
        missing = [column for column in wanted if column not in backend.header(sheet)]
        if missing:
            backend.add_columns(sheet, missing)
        backend.write_rows(sheet, [(position, wanted)])
        written = backend.read_rows(sheet)[position]
    if any(written.get(column) != value for column, value in wanted.items()):
        raise GuardViolation(13, f"ТЗ {args.id}: прочитано не то, что записано")
    out(f"хранилище: {label}")
    out(f"«{sheet}», {args.id}: записано 1, прочитано 1 — исполнитель «{executor}», ТЗ {len(text)} знаков")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="growth_engine.task_brief", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--id", required=True, help="номер гипотезы, например H-17")
    parser.add_argument("--executor", required=True, help="какой чат исполняет")
    parser.add_argument("--brief", required=True, help="файл с текстом ТЗ")
    parser.add_argument("--how", help="файл «Как это работает»: ключевая идея, что делает пользователь, почему заявка")
    add_store_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except GuardViolation as exc:
        print(f"❌ ТЗ не записано: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
