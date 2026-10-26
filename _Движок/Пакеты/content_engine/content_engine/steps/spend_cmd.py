"""`content-engine spend report [--days 30] | add <id>` — цена статей по видам расхода (Т17.4).

`add` — ручная строка для трат вне машины (картинки старым маршрутом), чтобы цена статьи была полной.
"""
from __future__ import annotations

import json

from ..core import spend
from ..core.number import ESTIMATE, FACT


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("spend", help="учёт расхода: report | add")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("report", help="цена каждой статьи и итог периода по видам")
    add_profile(p)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("add", help="ручная строка расхода вне машины")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--step", required=True)
    p.add_argument("--rub", type=float, required=True)
    p.add_argument("--status", choices=(FACT, ESTIMATE), required=True)
    p.add_argument("--source", required=True, help="откуда цена: прайс провайдера, счёт")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "add":
        print(spend.add(instance, args.id, args.step, "rub", args.rub, args.status, args.source))
        return 0
    data = spend.report(instance, args.days)
    text = spend.render(data)
    (instance.reports_dir() / "Расход.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(data, ensure_ascii=False, indent=1) if args.json else text)
    return 0
