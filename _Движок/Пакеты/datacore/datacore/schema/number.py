"""Каноническое число Ядра данных. К2: без периода, источника, статуса и даты съёма число не существует.
Поля совпадают с числом Движка роста по построению — общего кода нет, совпадение проверяет адаптер."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

from .errors import RuleViolation

SOURCE_CLASSES = ("A", "B", "C", "D", "E", "F")


class Status(str, Enum):
    FACT = "факт"
    ESTIMATE = "оценка"
    PROXY = "proxy"
    NO_DATA = "нет данных"


STATUS_RANK = {Status.NO_DATA: 0, Status.PROXY: 1, Status.ESTIMATE: 2, Status.FACT: 3}


@dataclass(frozen=True)
class Number:
    metric: str
    level: str
    scope: str
    flow: str
    period_start: date
    period_end: date            # граница исключающая
    source: str                 # «класс:система:запрос»
    status: Status
    value: float | None
    denominator: str | None = None
    # Само число знаменателя: «0,49 %» проверить нельзя, «49 из 10000» — можно. Без него ошибка
    # в определении знаменателя остаётся незаметной (ревью Codex этапа 5, п.7).
    denominator_value: float | None = None
    unit: str = "шт"
    missing: str = ""
    segment: str = ""
    as_of: date | None = None

    def __post_init__(self):
        for name in ("metric", "level", "scope", "flow", "source"):
            if not getattr(self, name):
                raise RuleViolation("К2", f"у числа пусто поле «{name}»")
        if not isinstance(self.status, Status):
            raise RuleViolation("К2", f"статус «{self.status}» не из канона")
        if type(self.as_of) is not date:
            raise RuleViolation("К2", "у числа нет даты съёма «as_of» (день)")
        if type(self.period_start) is not date or type(self.period_end) is not date:
            raise RuleViolation("К2", "период задаётся датами")
        if self.period_end <= self.period_start:
            raise RuleViolation("К2", "конец периода позже начала — правая граница исключающая")
        parts = self.source.split(":", 2)
        if len(parts) != 3 or parts[0] not in SOURCE_CLASSES or not parts[1] or not parts[2]:
            raise RuleViolation("К2", f"источник «{self.source}» не в формате «класс:система:запрос»")
        if self.status is Status.NO_DATA and self.value is not None:
            raise RuleViolation("К2", "«нет данных» не несёт значения — это не ноль")
        if self.status is not Status.NO_DATA and self.value is None:
            raise RuleViolation("К2", "значение пусто — статус обязан быть «нет данных»")
        if self.value is not None and (isinstance(self.value, bool) or not isinstance(self.value, (int, float))
                                       or not math.isfinite(self.value)):
            raise RuleViolation("К2", f"значение {self.value!r} — не конечное число")
        if self.segment and ("=" not in self.segment or self.segment.startswith("=") or self.segment.endswith("=")):
            raise RuleViolation("К2", f"сегмент «{self.segment}» не в формате «измерение=значение»")
        if self.unit == "доля" and not self.denominator:
            raise RuleViolation("К2", "доля без знаменателя — не факт")

    @property
    def source_class(self) -> str:
        return self.source.split(":", 2)[0]

    @property
    def source_system(self) -> str:
        return self.source.split(":", 2)[1]


def _merged_notes(numbers: list[Number]) -> list[str]:
    """Пояснения слагаемых без повторов, в порядке появления. Одна и та же причина (например, незавершённая
    загрузка общего источника) есть у каждого слагаемого и в сумме должна прозвучать один раз."""
    seen, out = set(), []
    for n in numbers:
        for part in (p.strip() for p in (n.missing or "").split(";")):
            if part and part not in seen:
                seen.add(part)
                out.append(part)
    return out


def add(numbers: list[Number], *, cross_scope_proof: str | None = None) -> Number:
    """Сумма чисел. К7: разные съёмы, ступени, метрики, потоки, разрезы, доли, кабинеты без доказательства
    и окна не встык не складываются. Итог — самый слабый статус; «нет данных» отравляет сумму."""
    if len(numbers) < 2:
        raise ValueError("сложение — минимум два числа")
    first = numbers[0]
    for x in numbers[1:]:
        if x.as_of != first.as_of:
            raise RuleViolation("К7", f"числа разных съёмов не складываются: {first.as_of} и {x.as_of}")
        for name in ("metric", "level", "flow", "segment", "unit"):
            if getattr(x, name) != getattr(first, name):
                raise RuleViolation("К7", f"разные «{name}»: {getattr(first, name)!r} и {getattr(x, name)!r}")
    if first.unit == "доля":
        raise RuleViolation("К7", "доли не складываются — считается заново от знаменателей")
    scopes = sorted({x.scope for x in numbers})
    if len(scopes) > 1 and not cross_scope_proof:
        raise RuleViolation("К7", f"сумма по кабинетам {scopes} без доказательства дедупа")
    if len(scopes) > 1:
        periods = {(x.period_start, x.period_end) for x in numbers}
        if len(periods) != 1:
            raise RuleViolation("К7", "при сумме по кабинетам периоды обязаны совпадать")
        period_start, period_end = first.period_start, first.period_end
    else:
        ordered = sorted(numbers, key=lambda x: x.period_start)
        for prev, nxt in zip(ordered, ordered[1:]):
            if nxt.period_start != prev.period_end:
                raise RuleViolation("К7", f"окна не встык: {prev.period_end} → {nxt.period_start}")
        period_start, period_end = ordered[0].period_start, ordered[-1].period_end
    status = min((x.status for x in numbers), key=STATUS_RANK.get)
    value = None if status is Status.NO_DATA else sum(x.value for x in numbers)
    return replace(first, scope="+".join(scopes), period_start=period_start, period_end=period_end,
                   status=status, value=value,
                   source=f"{first.source_class}:datacore:sum({len(numbers)})",
                   missing="; ".join(_merged_notes(numbers)))
