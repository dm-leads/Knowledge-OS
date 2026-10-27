"""Бюджет-гейт (Т17.2): любой платный вызов пакета — с ценой до вызова и «да» человека.

- `--unattended` (режим без человека) — ноль платных вызовов при любой цене;
- цена выше `budget.run_limit_rub` или месяц по `spend.jsonl` плюс цена выше `budget.month_limit_rub` — отказ и с «да»:
  потолок — не согласие; 0 в профиле — платные вызовы запрещены;
- без `--confirm-spend <сумма>` или сумма меньше цены — печать цены и отказ (код 2), ноль вызовов.
Пройденный гейт пишет строку в `spend.jsonl` после вызова.
"""
from __future__ import annotations

from typing import Callable

from . import spend
from .errors import BudgetRefused
from .number import ESTIMATE


def gate(instance, price_rub: float, *, confirm: float | None, unattended: bool, what: str) -> None:
    b = instance.p["budget"]
    if unattended:
        raise BudgetRefused(f"{what}: режим без человека (--unattended) — платные вызовы не выполняются "
                            f"(цена {price_rub:.2f} ₽)")
    if price_rub > b["run_limit_rub"]:
        raise BudgetRefused(f"{what}: цена {price_rub:.2f} ₽ выше потолка прогона budget.run_limit_rub = "
                            f"{b['run_limit_rub']} ₽ — не выполняется и с подтверждением")
    month = spend.month_rub(instance)
    if month + price_rub > b["month_limit_rub"]:
        raise BudgetRefused(f"{what}: за месяц уже {month:.2f} ₽, с этим вызовом {month + price_rub:.2f} ₽ — выше "
                            f"budget.month_limit_rub = {b['month_limit_rub']} ₽")
    if confirm is None or confirm < price_rub:
        raise BudgetRefused(f"{what}: цена {price_rub:.2f} ₽ — нужно «да» человека: повторите с --confirm-spend "
                            f"{price_rub:.2f}")


def paid_call(instance, topic_id: str, step: str, price_rub: float, call: Callable[[], object], *,
              confirm: float | None, unattended: bool, what: str, source: str):
    """Гейт → вызов → строка расхода (оценка по прайсу провайдера; факт пишет адаптер, если цена пришла в ответе)."""
    gate(instance, price_rub, confirm=confirm, unattended=unattended, what=what)
    try:
        result = call()
    except Exception:
        # прогон оборвался — часть вызовов уже оплачена: расход пишется оценкой, месячный потолок не занижается
        spend.add(instance, topic_id, step, "rub", round(price_rub, 2), ESTIMATE, source + " (прогон оборвался)")
        raise
    spend.add(instance, topic_id, step, "rub", round(price_rub, 2), ESTIMATE, source)
    return result


def add_flags(parser):
    parser.add_argument("--confirm-spend", type=float, help="«да» человека на платный прогон: сумма, ₽")
    parser.add_argument("--unattended", action="store_true", help="режим без человека: ноль платных вызовов")
