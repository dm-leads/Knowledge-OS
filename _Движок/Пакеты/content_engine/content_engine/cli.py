"""Команда `content-engine`: подкоманды регистрирует каждый шаг сам (`register`).

Коды выхода общие: 0 — успех или «свободно», 1 — найдена проблема (пересечение, черновик, запас на пороге,
отказ запрета в коде: тема без гейта, ступень прав, блокировка, нет снимка «до»), 2 — «спорно» и отказ бюджет-гейта
без «да» человека, 3 — ошибка входа или источника, запись без подтверждения. Перед любой командой с профилем
истёкшие брони освобождаются (Т11.5).
"""
from __future__ import annotations

import argparse
import sys

from .core.errors import BudgetRefused, EngineError, GateRefused, WriteUnconfirmed

STEPS = ("check", "profile_cmd", "demand", "cluster", "demand_map", "reservations", "gate_topic", "queue",
         "gate_article", "known_answers", "publish", "spend_cmd", "prepare", "assemble", "indexing", "judge", "write", "review", "images", "onboard",
         "catalog_cmd", "status_cmd", "pulse", "bets_cmd", "economics", "audit", "schedule", "week_plan",
         "refresh", "ai_visibility", "behavior", "freeze", "competitors")


def build_parser() -> argparse.ArgumentParser:
    from importlib import import_module
    parser = argparse.ArgumentParser(prog="content-engine", description="Контент-машина: анти-дубли, очередь, гейты")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in STEPS:
        module = import_module(f"content_engine.steps.{name}")
        module.register(sub)
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "unattended", False):
        return _run(args)
    # запуск без человека (расписание, Т42.2): строка в журнал запусков инстанса — по ней `schedule list` и `check`
    # видят, когда задание отработало последний раз
    from datetime import datetime
    start = datetime.now().isoformat(timespec="seconds")
    code = _run(args)
    try:
        from pathlib import Path
        from .core import store
        command = " ".join(x for x in (args.command, getattr(args, "action", "") or "") if x)
        root = Path(args.profile).resolve().parent
        store.append_jsonl(root / "data" / "schedule_runs.jsonl", [{"command": command, "start": start, "code": code}])
        if code:                             # Т42.2: остановка без человека — строкой в отчёт дня
            day = root / "reports" / start[:10]
            day.mkdir(parents=True, exist_ok=True)
            with open(day / "unattended.md", "a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"- {start} `{command}` — код {code}: ждёт человека или ошибка; см. вывод команды\n")
    except Exception as exc:                 # журнал запусков не должен ломать сам запуск
        print(f"журнал запусков не записан: {exc}", file=sys.stderr)
    return code


def _run(args) -> int:
    try:
        return int(args.func(args) or 0)
    except GateRefused as exc:
        print(f"отказ: {exc}", file=sys.stderr)
        return 1
    except BudgetRefused as exc:
        print(f"бюджет-гейт: {exc}", file=sys.stderr)
        return 2
    except WriteUnconfirmed as exc:
        print(f"запись не подтверждена: {exc}", file=sys.stderr)
        return 3
    except EngineError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:                 # код 1 значит «найдена проблема» — сбой им быть не должен
        print(f"ошибка: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


def add_profile(parser):
    parser.add_argument("--profile", required=True, help="путь к profile.yaml инстанса")


def open_instance(args, *, read_only: bool = False, **kwargs):
    """Инстанс команды. С записью — сначала освобождаются истёкшие брони (побочная запись, она в контракте операций);
    `read_only` — ничего не освобождается и не создаётся (`status`)."""
    from .core.instance import Instance
    from .steps.reservations import expire
    instance = Instance(args.profile, **kwargs)
    if read_only:
        instance.read_only = True
        return instance
    # платный запасной источник выдачи (Н6) берёт «да» человека из флагов команды
    instance.confirm_spend = getattr(args, "confirm_spend", None)
    instance.unattended = getattr(args, "unattended", False)
    if instance.unattended:                  # расписание не пишет очередь и брони параллельно с чатом контента
        return instance
    released = expire(instance)
    if released:
        print(f"брони истекли и освобождены: {released}")
    return instance
