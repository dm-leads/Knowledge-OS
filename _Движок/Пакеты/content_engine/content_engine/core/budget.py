"""Бюджет-гейт (Т17.2): любой платный вызов пакета — с ценой до вызова и «да» человека.

- `--unattended` (режим без человека) — ноль платных вызовов при любой цене;
- цена выше `budget.run_limit_rub` или месяц по `spend.jsonl` плюс цена выше `budget.month_limit_rub` — отказ и с «да»:
  потолок — не согласие; 0 в профиле — платные вызовы запрещены;
- без `--confirm-spend <сумма>` или сумма меньше цены — печать цены и отказ (код 2), ноль вызовов.
Пройденный гейт пишет строку в `spend.jsonl` после вызова.

Потолок прогона и «да» человека — на всю команду, а не на один вызов: платные вызовы одной команды (пачки выдачи)
складываются (`instance._run_spent`). Проверка, вызов и запись расхода — под блокировкой бюджета инстанса: два
параллельных платных прогона не проходят месячный потолок вдвоём. Цена — конечное неотрицательное число.
"""
from __future__ import annotations

import math
from typing import Callable

from . import spend
from .errors import BudgetRefused
from .number import ESTIMATE


def gate(instance, price_rub: float, *, confirm: float | None, unattended: bool, what: str) -> None:
    b = instance.p["budget"]
    if isinstance(price_rub, bool) or not isinstance(price_rub, (int, float)) or not math.isfinite(price_rub) \
            or price_rub < 0:
        raise BudgetRefused(f"{what}: цена {price_rub!r} — не конечное неотрицательное число, вызов не выполняется")
    if confirm is not None and (not math.isfinite(confirm) or confirm < 0):
        raise BudgetRefused(f"{what}: --confirm-spend {confirm!r} — не конечное неотрицательное число")
    spent = getattr(instance, "_run_spent", 0.0)        # уже потрачено этой командой
    if unattended:
        raise BudgetRefused(f"{what}: режим без человека (--unattended) — платные вызовы не выполняются "
                            f"(цена {price_rub:.2f} ₽)")
    if spent + price_rub > b["run_limit_rub"]:
        raise BudgetRefused(f"{what}: цена {price_rub:.2f} ₽ (с уже потраченным в прогоне — {spent + price_rub:.2f} ₽) "
                            f"выше потолка прогона budget.run_limit_rub = {b['run_limit_rub']} ₽ — не выполняется и "
                            "с подтверждением")
    month = spend.month_rub(instance)
    if month + price_rub > b["month_limit_rub"]:
        raise BudgetRefused(f"{what}: за месяц уже {month:.2f} ₽, с этим вызовом {month + price_rub:.2f} ₽ — выше "
                            f"budget.month_limit_rub = {b['month_limit_rub']} ₽")
    if confirm is None or confirm < spent + price_rub:
        raise BudgetRefused(f"{what}: цена {price_rub:.2f} ₽ (за прогон — {spent + price_rub:.2f} ₽) — нужно «да» "
                            f"человека: повторите с --confirm-spend {spent + price_rub:.2f}")


def paid_call(instance, topic_id: str, step: str, price_rub: float, call: Callable[[], object], *,
              confirm: float | None, unattended: bool, what: str, source: str):
    """Гейт → вызов → строка расхода (оценка по прайсу провайдера; факт пишет адаптер, если цена пришла в ответе)."""
    from . import locks
    with locks.held(instance, "budget", "spend"):      # проверка, вызов и запись расхода — одним куском
        gate(instance, price_rub, confirm=confirm, unattended=unattended, what=what)
        instance._run_spent = getattr(instance, "_run_spent", 0.0) + price_rub
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
