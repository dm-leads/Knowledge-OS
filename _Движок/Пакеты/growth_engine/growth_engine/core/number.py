"""Каноническое число слоя данных. Страж 1: без периода, источника, статуса и даты съёма число не принимается."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum

from .errors import GuardViolation

SOURCE_CLASSES = ("A", "B", "C", "D", "E", "F")


class Status(str, Enum):
    FACT = "факт"
    ESTIMATE = "оценка"
    PROXY = "proxy"
    NO_DATA = "нет данных"


# Итог операции над числами получает самый слабый статус из входящих.
STATUS_RANK = {Status.NO_DATA: 0, Status.PROXY: 1, Status.ESTIMATE: 2, Status.FACT: 3}


@dataclass(frozen=True)
class Number:
    metric: str               # канонический смысл из конфигурации инстанса
    level: str                # ступень воронки или природа величины
    scope: str                # кабинет, проект или компания
    flow: str                 # поток из конфигурации: web / no_visit / all ...
    period_start: date
    period_end: date          # граница исключающая
    source: str               # «<класс A–F>:<система>:<запрос>»
    status: Status
    value: float | None
    denominator: str | None = None
    unit: str = "шт"
    missing: str = ""         # чего не хватает — для стандарта ответа (страж 9)
    segment: str = ""         # разрез «измерение=значение»; пусто — без разреза
    as_of: date | None = None # дата съёма (день): числа разных съёмов не складываются и не делятся (П3)

    def __post_init__(self):
        for name in ("metric", "level", "scope", "flow", "source"):
            if not getattr(self, name):
                raise GuardViolation(1, f"у числа пусто поле «{name}»")
        if not isinstance(self.status, Status):
            raise GuardViolation(1, f"статус «{self.status}» не из канона")
        if type(self.as_of) is not date:
            raise GuardViolation(1, "у числа нет даты съёма «as_of» (день): дрейф задним числом не отличить (П3)")
        if not (isinstance(self.period_start, date) and isinstance(self.period_end, date)):
            raise GuardViolation(1, "период задаётся датами")
        if self.period_end <= self.period_start:
            raise GuardViolation(1, "конец периода должен быть позже начала (граница исключающая)")
        parts = self.source.split(":", 2)
        if len(parts) != 3 or parts[0] not in SOURCE_CLASSES or not parts[1] or not parts[2]:
            raise GuardViolation(1, f"источник «{self.source}» не в формате «класс:система:запрос»")
        if self.status is Status.NO_DATA and self.value is not None:
            raise GuardViolation(1, "«нет данных» не несёт значения — это не ноль")
        if self.status is not Status.NO_DATA and self.value is None:
            raise GuardViolation(1, "значение пусто — статус обязан быть «нет данных»")
        if self.value is not None and (isinstance(self.value, bool)
                                       or not isinstance(self.value, (int, float))
                                       or not math.isfinite(self.value)):
            raise GuardViolation(1, f"значение {self.value!r} — не конечное число")
        if self.segment:
            dimension, _, value = self.segment.partition("=")
            if not dimension or not value:
                raise GuardViolation(1, f"сегмент «{self.segment}» не в формате «измерение=значение»")
        if self.unit == "доля" and not self.denominator:
            raise GuardViolation(9, "доля без знаменателя — не факт")

    @property
    def source_class(self) -> str:
        return self.source.split(":", 2)[0]

    @property
    def source_system(self) -> str:
        return self.source.split(":", 2)[1]
