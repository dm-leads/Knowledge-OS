"""Каталог операций (шаг 4): `content-engine catalog [--json | --md]` — контракт всех команд этого выпуска.

Навык `content-engine` читает каталог того выпуска, которым идёт работа; сервер MCP строит из `--json` инструменты.
Подкоманда без контракта или контракт без подкоманды — ошибка (код 3): каталог неполон, полагаться на него нельзя.
"""
from __future__ import annotations

import json


def register(sub):
    p = sub.add_parser("catalog", help="каталог операций выпуска: контракт каждой команды (--json для сервера MCP)")
    p.add_argument("--json", action="store_true", help="машиночитаемо")
    p.add_argument("--md", action="store_true", help="для людей: таблица и карточки операций")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..core import contract
    from ..core.errors import EngineError
    cat = contract.catalog()
    if cat["missing"] or cat["extra"]:
        raise EngineError(f"каталог неполон: без контракта {cat['missing']}, контракт без команды {cat['extra']}")
    if args.json:
        print(json.dumps(cat, ensure_ascii=False, indent=1))
    elif args.md:
        print(contract.markdown(cat), end="")
    else:
        print(f"Каталог операций выпуска {cat['engine_version']}: {len(cat['operations'])} операций")
        for op in cat["operations"]:
            print(f"  {op['command']:<20} {op['name']}")
    return 0
