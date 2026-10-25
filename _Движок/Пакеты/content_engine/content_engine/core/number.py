"""Число реестра: значение не живёт без периода, источника, статуса и даты съёма (Т3.3)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .errors import StoreError

FACT, ESTIMATE, PROXY, NO_DATA = "факт", "оценка", "proxy", "нет данных"
STATUSES = (FACT, ESTIMATE, PROXY, NO_DATA)


@dataclass(frozen=True)
class Number:
    value: float | None
    period_from: str          # ISO-дата начала, включительно
    period_to: str            # ISO-дата конца, включительно
    source: str               # «система:запрос»
    status: str
    as_of: str                # ISO-дата съёма
    note: str = ""            # ступень лесенки ценности, причина «нет данных»

    def __post_init__(self):
        if self.status not in STATUSES:
            raise StoreError(f"статус числа «{self.status}» не из списка {STATUSES}")
        if not self.source or not self.as_of:
            raise StoreError("у числа пусто поле «source» или «as_of»")
        for name in ("period_from", "period_to", "as_of"):
            date.fromisoformat(getattr(self, name))
        if self.status == NO_DATA and self.value is not None:
            raise StoreError("«нет данных» не несёт значения — это не ноль")
        if self.status != NO_DATA and (self.value is None or isinstance(self.value, bool)
                                       or not math.isfinite(float(self.value))):
            raise StoreError(f"значение {self.value!r} — не конечное число при статусе «{self.status}»")

    def to_dict(self) -> dict:
        return {"value": self.value, "period_from": self.period_from, "period_to": self.period_to,
                "source": self.source, "status": self.status, "as_of": self.as_of, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict) -> "Number":
        return cls(**data)

    @classmethod
    def missing(cls, source: str, period_from: str, period_to: str, as_of: str, reason: str) -> "Number":
        return cls(None, period_from, period_to, source, NO_DATA, as_of, reason)
