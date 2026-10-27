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
         "gate_article", "known_answers", "publish", "spend_cmd", "prepare", "assemble", "indexing", "judge", "write", "review", "images", "onboard")


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


def open_instance(args, **kwargs):
    from .core.instance import Instance
    from .steps.reservations import expire
    instance = Instance(args.profile, **kwargs)
    # платный запасной источник выдачи (Н6) берёт «да» человека из флагов команды
    instance.confirm_spend = getattr(args, "confirm_spend", None)
    instance.unattended = getattr(args, "unattended", False)
    released = expire(instance)
    if released:
        print(f"брони истекли и освобождены: {released}")
    return instance
