"""Окна периода по календарным месяцам: правая граница исключающая, переход через год — обычный.

Перенесено из проверки аддитивности 28.09.2026: окна нужны мосту, когорте и экономике, а общие команды не должны
тянуть за ними модуль, привязанный к адаптеру системы.
"""
from __future__ import annotations

from datetime import date


def month_windows(start: str, months: int) -> list[tuple[date, date]]:
    """`months` окон подряд с месяца `start` (ГГГГ-ММ): [(первое число, первое число следующего месяца), …]."""
    year, month = int(start[:4]), int(start[5:7])
    windows = []
    for _ in range(months):
        first = date(year, month, 1)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        windows.append((first, date(year, month, 1)))
    return windows
