"""Шесть артефактов контракта рядом с реестром гипотез и закрытие цикла.

Стражи: 9 (маршрут без молчаливых допущений), 12 (в карте источников — имена переменных),
13 (подключённый источник обязан иметь пробу), 14 (решение и строка знаний), 15 (вычитание).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .errors import GuardViolation
from .number import SOURCE_CLASSES, STATUS_RANK, Number, Status
from .registry import MODEL_SYSTEM, HStatus

SOURCE_STATUSES = ("подключён", "есть, нет доступа", "нет", "не применимо")
ROUTE_DECISIONS = ("есть", "заменитель", "добыча", "нет данных")
KNOWLEDGE_VERDICTS = ("подтверждено", "опровергнуто", "открыто")
CYCLE_STEPS = tuple(range(1, 11))
ALGORITHM_STEPS = tuple(range(0, 11))   # шаг 0 допустим в маршруте как инкрементальное обнаружение
AGGREGATE_FLOW = "all"                  # поток-агрегат: ветки по потокам складываются только в него
_ENV_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)+$")
_ENV_NAME_MAX = 40


@dataclass(frozen=True)
class SourceMapEntry:
    name: str
    source_class: str
    status: str
    history_from: str
    truth_point: str
    probe: str
    traps: tuple[str, ...] = ()
    secret_env_names: tuple[str, ...] = ()

    def __post_init__(self):
        if self.source_class not in SOURCE_CLASSES:
            raise GuardViolation(13, f"источник «{self.name}»: класс не из A–F")
        if self.status not in SOURCE_STATUSES:
            raise GuardViolation(13, f"источник «{self.name}»: статус «{self.status}» не из канона")
        if self.status == "подключён":
            missing = [f for f in ("probe", "truth_point", "history_from") if not getattr(self, f)]
            if missing:
                raise GuardViolation(13, f"источник «{self.name}» подключён, но пусто: {', '.join(missing)}",
                                     GuardViolation.COVERAGE)
        for env_name in self.secret_env_names:
            if len(env_name) > _ENV_NAME_MAX or not _ENV_NAME.match(env_name):
                raise GuardViolation(12, f"источник «{self.name}»: в секретах допустимо только имя переменной")


def check_secret_names(entries, known_env_names) -> None:
    """Страж 12: имя переменной обязано существовать среди имён файла секретов.
    Имена передаёт вызывающий (ключи файла окружения без значений); значение в ошибку не попадает."""
    known = set(known_env_names)
    for entry in entries:
        unknown = [x for x in entry.secret_env_names if x not in known]
        if unknown:
            raise GuardViolation(12, f"источник «{entry.name}»: {len(unknown)} записей в секретах "
                                     f"не найдены среди имён переменных")


@dataclass(frozen=True)
class RouteStep:
    step: int
    classes: dict                          # класс A–F → решение маршрутизатора
    substitute: str = ""
    extraction_cost: str = ""
    skipped_reason: str = ""
    no_data_reason: str = ""                # «нет данных» по классу — только с причиной


@dataclass(frozen=True)
class CycleRoute:
    cycle_id: str
    goal: str
    written_on: date
    expected_confidence: str
    steps: tuple[RouteStep, ...]
    main_class: str = ""                    # класс источника главной метрики цикла — для шагов 6, 9, 10 (Р2)
    notes: tuple[str, ...] = ()             # правила формы бизнеса и стартового маршрута: что заменяем, что пропускаем
    closed_at: int = 0                      # шаг, на котором маршрут закрыт выводом; дальше шаги не исполняются
    closed_reason: str = ""                 # вывод, которым закрыт маршрут; шаги после закрытия — только по нему
    sales_goal: bool = False                # цель цикла — продажи: на шаге 9 обязателен класс D (Р2)


def validate_route(route: CycleRoute, class_status: dict) -> None:
    """Маршрут пишется до исполнения: у каждого шага 1–10 — классы источников или причина пропуска.
    «Есть» — только для подключённого класса; подключённый класс может требовать добычи, если нужной
    ступени в нём нет (например, событий блоков в веб-аналитике). Шаг с классами перечисляет хотя бы свои
    обязательные классы (таблица Р2); заявленная уверенность вывода не выше той, что дают решения."""
    from .router import CONFIDENCE, derive_confidence, required_classes   # маршрутизатор строит маршрут из артефактов

    numbers = [s.step for s in route.steps]
    doubled = sorted({x for x in numbers if numbers.count(x) > 1})
    if doubled:
        raise GuardViolation(9, f"маршрут {route.cycle_id}: шаги описаны дважды: {doubled}")
    outside = sorted(set(numbers) - set(ALGORITHM_STEPS))
    if outside:
        raise GuardViolation(9, f"маршрут {route.cycle_id}: шаги вне алгоритма 0–10: {outside}")
    if route.main_class not in SOURCE_CLASSES:
        raise GuardViolation(9, f"маршрут {route.cycle_id}: класс главной метрики «{route.main_class}» не из A–F")
    if route.closed_at:
        later = [s for s in route.steps if s.step > route.closed_at]
        if (route.closed_at not in CYCLE_STEPS or not route.closed_reason.strip()
                or any(s.classes or s.skipped_reason != route.closed_reason for s in later)):
            raise GuardViolation(9, f"маршрут {route.cycle_id}: закрыт на шаге {route.closed_at} — шаг вне 1–10, нет "
                                    "причины закрытия или после него шаги с классами либо с другой причиной пропуска")
    by_step = {s.step: s for s in route.steps}
    for number in CYCLE_STEPS:
        s = by_step.get(number)
        if s is None:
            raise GuardViolation(9, f"маршрут {route.cycle_id}: шаг {number} не описан")
        if not s.classes and not s.skipped_reason:
            raise GuardViolation(9, f"маршрут {route.cycle_id}: шаг {number} без классов и без причины пропуска")
        for cls, decision in s.classes.items():
            if cls not in SOURCE_CLASSES or decision not in ROUTE_DECISIONS:
                raise GuardViolation(9, f"маршрут {route.cycle_id}, шаг {number}: «{cls} → {decision}» не из канона")
            if decision == "есть" and class_status.get(cls) != "подключён":
                raise GuardViolation(9, f"маршрут {route.cycle_id}, шаг {number}: класс {cls} не подключён")
            if decision == "заменитель" and not s.substitute:
                raise GuardViolation(9, f"маршрут {route.cycle_id}, шаг {number}: заменитель не назван")
            if decision == "добыча" and not s.extraction_cost:
                raise GuardViolation(9, f"маршрут {route.cycle_id}, шаг {number}: у добычи нет оценки цены")
            if decision == "нет данных" and not s.no_data_reason:
                raise GuardViolation(9, f"маршрут {route.cycle_id}, шаг {number}: «нет данных» по классу {cls} "
                                        f"без причины")
        for group in (required_classes(number, route.main_class, route.sales_goal) if s.classes else ()):
            if not set(group) & set(s.classes):
                raise GuardViolation(9, f"маршрут {route.cycle_id}, шаг {number}: нет решения по обязательному "
                                        f"классу {' или '.join(group)}")
    if route.expected_confidence not in CONFIDENCE:
        raise GuardViolation(9, f"маршрут {route.cycle_id}: ожидаемая уверенность «{route.expected_confidence}» "
                                f"не из: {', '.join(CONFIDENCE)}")
    derived = derive_confidence(route.steps, route.main_class, route.closed_at, route.sales_goal)
    if CONFIDENCE.index(route.expected_confidence) > CONFIDENCE.index(derived):
        raise GuardViolation(9, f"маршрут {route.cycle_id}: заявлена уверенность «{route.expected_confidence}», "
                                f"а решения дают «{derived}»")


@dataclass(frozen=True)
class Branch:
    id: str
    name: str
    ceiling: Number                        # потолок ветки в единицах цели
    hypothesis_ids: tuple[str, ...] = ()
    # Единица действия, принятая шагом 3 на этой ветке. Канон связывает шаги 3 и 6 именно через неё: ворота шага 3 —
    # «узкое место названо как метрика, а не как единица действия → не принято», ворота шага 6 — «нет метрики, факта
    # или единицы действия → брак». Поле необязательное: дерево, записанное до шага 3, остаётся законным.
    unit_of_action: UnitOfAction | None = None


@dataclass(frozen=True)
class GoalTree:
    goal: Number
    branches: tuple[Branch, ...]

    def gap(self) -> Number:
        """Цель минус сумма потолков веток. Потолки могут пересекаться, поэтому разрыв — не сильнее «оценки»."""
        g = self.goal
        for b in self.branches:
            c = b.ceiling
            if (c.metric, c.unit, c.scope, c.period_start, c.period_end, c.as_of) != \
                    (g.metric, g.unit, g.scope, g.period_start, g.period_end, g.as_of):
                raise GuardViolation(3, f"ветка {b.id}: потолок не в единицах, кабинете, окне или съёме цели")
            if c.flow != g.flow and g.flow != AGGREGATE_FLOW:
                raise GuardViolation(3, f"ветка {b.id}: поток «{c.flow}» не входит в поток цели «{g.flow}»")
            if (c.source_class, c.source_system) != (g.source_class, g.source_system) and c.source_system != MODEL_SYSTEM:
                raise GuardViolation(3, f"ветка {b.id}: потолок из системы «{c.source_system}», а цель — из "
                                        f"«{g.source_system}»; потолок берётся из системы цели или из модели")
        status = min([g.status] + [b.ceiling.status for b in self.branches], key=lambda s: STATUS_RANK[s])
        if STATUS_RANK[status] > STATUS_RANK[Status.ESTIMATE]:
            status = Status.ESTIMATE
        common = dict(metric=f"{g.metric}: разрыв дерева цели", level=g.level, scope=g.scope, flow=g.flow,
                      period_start=g.period_start, period_end=g.period_end, source=g.source, unit=g.unit,
                      as_of=g.as_of)
        if status is Status.NO_DATA:
            return Number(**common, status=status, value=None, missing="у цели или ветки нет данных")
        return Number(**common, status=status, value=g.value - sum(b.ceiling.value for b in self.branches),
                      missing="потолки веток могут пересекаться — разрыв оценочный")


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    statement: str
    verdict: str
    on: date
    source: str
    hypothesis_id: str

    def __post_init__(self):
        if self.verdict not in KNOWLEDGE_VERDICTS:
            raise GuardViolation(14, f"знание {self.id}: вердикт «{self.verdict}» не из канона")
        if not self.source:
            raise GuardViolation(1, f"знание {self.id}: нет источника")


@dataclass(frozen=True)
class DecisionEntry:
    cycle_id: str
    decided: str
    why: str
    subtraction: str                       # что убрали: гипотезу, сегмент, канал, допущение, шаг
    alternatives: tuple[str, ...] = ()
    id: str = ""                           # номер записи журнала: хранилище требует его (Х3)


def close_cycle(cycle_id: str, hypotheses, knowledge, decisions) -> None:
    """Цикл закрывается решением со строкой знаний (страж 14) и письменным вычитанием (страж 15).
    Гипотезы в тесте или в отложенном замере переходят в следующий цикл — длинный цикл сделки
    не должен держать закрытие."""
    foreign = [h.id for h in hypotheses if h.cycle_id != cycle_id]
    if foreign:
        raise GuardViolation(14, f"цикл {cycle_id}: переданы гипотезы другого цикла: {', '.join(foreign)}")
    decided = [h for h in hypotheses if h.status in (HStatus.CONCLUDED, HStatus.ARCHIVE) and h.decision]
    if not decided:
        raise GuardViolation(14, f"цикл {cycle_id}: нет гипотезы с решением scale / iterate / kill / research")
    rows = {k.id: k for k in knowledge}
    for h in decided:
        row = rows.get(h.knowledge_row)
        if row is None or row.hypothesis_id != h.id:
            raise GuardViolation(14, f"цикл {cycle_id}: у {h.id} нет своей строки в карте знаний")
    if not any(d.cycle_id == cycle_id and d.subtraction.strip() for d in decisions):
        raise GuardViolation(15, f"цикл {cycle_id}: не записано, что убрали")
