"""Узкое место цикла (шаг 3 канона): одно, на единице действия, в порядке «маржа → чек → конверсия → поток».

Канон, шаг 3: «одно; где копится запас перед шагом; плечо; порядок кандидатов маржа → чек → конверсия → поток; спуск
по лестнице глубины до единицы действия». Ворота шага: узкое место, названное метрикой, а не единицей действия, не
принимается (страж 6).

Почему порядок кандидатов — правило, а не подсказка: маржа и чек меняют деньги на той же единице, поэтому рост
конверсии или потока при просевшей марже несёт меньше денег, чем кажется. Поэтому кандидат выбирается первым из тех,
кто ухудшился, в объявленном порядке, а не «самый большой по величине падения».

Что модуль НЕ делает: не считает деньги (это движок экономики), не ходит в источники, не пишет в хранилище.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .arithmetic import ratio, render
from .config import InstanceConfig
from .errors import GuardViolation
from .ladders import UnitOfAction
from .number import Number, Status

# Порядок кандидатов канона: первым берётся ухудшившийся кандидат, стоящий раньше в этом списке.
class Candidate(str, Enum):
    MARGIN = "маржа"
    CHECK = "чек"
    CONVERSION = "конверсия"
    FLOW = "поток"


@dataclass(frozen=True)
class StageGap:
    """Переход между соседними объявленными ступенями воронки и его конверсия."""
    upper: Number
    lower: Number
    conversion: Number


def _ordered(numbers: list[Number], cfg: InstanceConfig) -> list[Number]:
    """Ступени по порядку `cfg.levels`. Метрика без объявления — страж 3 из `cfg.rule()`."""
    for x in numbers:
        rule = cfg.rule(x.metric)          # метрика вне конфигурации — страж 3
        if rule.level != x.level:
            raise GuardViolation(3, f"«{x.metric}»: уровень «{x.level}» не совпадает с объявленным «{rule.level}»")
        if x.level not in cfg.levels:
            raise GuardViolation(3, f"«{x.metric}»: ступень «{x.level}» не объявлена в levels")
    return sorted(numbers, key=lambda x: cfg.levels.index(x.level))


def _conversion(upper: Number, lower: Number, cfg: InstanceConfig) -> Number:
    """Конверсия перехода. Ступень, не складывающаяся по окнам, даёт «нет данных» с причиной, а не ноль."""
    metric = f"cr_{upper.metric}_{lower.metric}"
    for x in (upper, lower):
        if not cfg.rule(x.metric).period_additive:
            return replace(upper, metric=metric, level=f"{upper.level}/{lower.level}", unit="доля",
                           denominator=upper.metric, value=None, status=Status.NO_DATA,
                           missing=f"«{x.metric}» не складывается по окнам: конверсию перехода на окне не считаем")
    # Стражи совпадения периода, кабинета, потока, источника, съёма и порядка ступеней — в ratio() арифметики.
    return ratio(lower, upper, metric, cfg)


def stage_gaps(numbers: list[Number], cfg: InstanceConfig) -> list[StageGap]:
    """Переходы между соседними ступенями: где копится запас перед шагом."""
    stages = _ordered(numbers, cfg)
    if len(stages) < 2:
        raise GuardViolation(9, "узкое место ступеней: нужно не меньше двух объявленных ступеней воронки")
    return [StageGap(upper, lower, _conversion(upper, lower, cfg))
            for upper, lower in zip(stages, stages[1:])]


def _fell(now: Number, before: Number, name: str) -> bool | None:
    """Ухудшился ли кандидат. `нет данных` — не «в порядке»: возвращается None, кандидат остаётся непроверенным."""
    if now.value is None or before.value is None:
        return None
    if (now.unit, now.denominator) != (before.unit, before.denominator):
        raise GuardViolation(4, f"«{name}»: сравниваются величины разной природы ({now.unit} / {before.unit})")
    return now.value < before.value


@dataclass(frozen=True)
class Bottleneck:
    """Выбранное узкое место. Принимается только с единицей действия (страж 6)."""
    candidate: Candidate
    gap: StageGap | None
    evidence: tuple[Number, ...]
    missing: str = ""
    unit_of_action: UnitOfAction | None = None

    @property
    def line(self) -> str:
        """Одна строка узкого места: кандидат, переход и единица действия — для стандарта ответа (страж 9)."""
        where = f"{self.gap.upper.metric} → {self.gap.lower.metric}" if self.gap else self.candidate.value
        unit = "" if self.unit_of_action is None else \
            "; единица действия: " + ", ".join(f"{k}={v}" for k, v in self.unit_of_action.coordinates.items())
        numbers = "; ".join(render(x) for x in self.evidence)
        return f"узкое место: {self.candidate.value} на переходе {where}{unit}; {numbers}"

    def with_unit(self, unit: UnitOfAction) -> "Bottleneck":
        """Спуск по лестнице глубины завершён: узкое место названо координатами нижней ступени класса."""
        missing = "; ".join(p for p in self.missing.split("; ") if p and "единица действия" not in p)
        return replace(self, unit_of_action=unit, missing=missing)

    def accept(self) -> "Bottleneck":
        """Ворота шага 3: без единицы действия узкое место не принимается."""
        if self.unit_of_action is None:
            raise GuardViolation(6, f"узкое место «{self.candidate.value}» названо метрикой, а не единицей действия: "
                                    "нужны координаты нижней ступени класса источника")
        return self


def choose(gaps: list[StageGap], cfg: InstanceConfig, margin: Number, margin_before: Number, avg_check: Number,
           avg_check_before: Number, stage_before: list[StageGap] | None = None) -> Bottleneck:
    """Одно узкое место в порядке канона: маржа → чек → конверсия → поток.

    Конверсия считается ухудшившейся, если хуже стала самая узкая ступень (сравнение с `stage_before`); без прошлого
    окна конверсия — кандидат только тогда, когда деньги на единице не ухудшились и поток не просел.
    """
    missing = ["единица действия не названа"]
    money = ((Candidate.MARGIN, margin, margin_before), (Candidate.CHECK, avg_check, avg_check_before))
    for candidate, now, before in money:
        fell = _fell(now, before, candidate.value)
        if fell is None:
            missing.append(f"{candidate.value}: {now.missing or 'нет данных'} — кандидат не проверен")
            continue
        if fell:
            return Bottleneck(candidate, _narrowest(gaps), (now, before), "; ".join(missing))

    narrow = _narrowest(gaps)
    if stage_before is not None:
        was = {(g.upper.metric, g.lower.metric): g for g in stage_before}
        key = (narrow.upper.metric, narrow.lower.metric)
        old = was.get(key)
        if old is not None and narrow.conversion.value is not None and old.conversion.value is not None:
            if narrow.conversion.value < old.conversion.value:
                return Bottleneck(Candidate.CONVERSION, narrow, (narrow.conversion, old.conversion),
                                  "; ".join(missing))
            top = _top(gaps)
            old_top = next((g.upper for g in stage_before if g.upper.metric == top.metric), None)
            if old_top is not None and old_top.value is not None and top.value is not None \
                    and top.value < old_top.value:
                return Bottleneck(Candidate.FLOW, narrow, (top, old_top), "; ".join(missing))
    return Bottleneck(Candidate.CONVERSION, narrow, (narrow.conversion,), "; ".join(missing))


def _narrowest(gaps: list[StageGap]) -> StageGap:
    """Переход с наименьшей конверсией среди посчитанных: «нет данных» не сравнивается с числом."""
    known = [g for g in gaps if g.conversion.value is not None]
    if not known:
        raise GuardViolation(9, "узкое место: ни одна конверсия перехода не посчитана — нет данных")
    return min(known, key=lambda g: g.conversion.value)


def _top(gaps: list[StageGap]) -> Number:
    """Верхняя ступень воронки — вход потока."""
    return gaps[0].upper
