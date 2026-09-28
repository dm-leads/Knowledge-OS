"""Маршрутизатор (слой 4): путь цикла из цели, формы бизнеса и карты источников — черновик «Маршрута цикла».

Таблица «шаг → классы источников» — решение Р2 (кандидат, подтверждено Дмитрием 14.09.2026); обязательные классы
шага проверяет `validate_route()`. Черновик берёт класс, если он подключён; обязательный класс без подключения
получает заменитель из таблицы — добычу с ценой назначает человек. Ожидаемая уверенность следует из решений.
Модуль не знает имён систем, кабинетов и полей источников.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .artifacts import CYCLE_STEPS, CycleRoute, RouteStep, validate_route
from .config import BUSINESS_FORM
from .errors import GuardViolation

CLASSES = ("A", "B", "C", "D", "E", "F")
MAIN = "главная метрика"      # класс главной метрики цикла; у шагов 6, 9, 10 подставляется из маршрута
CONNECTED = "подключён"
CONFIDENCE = ("низкая", "средняя", "высокая")
_DECISION_RANK = {"нет данных": 0, "заменитель": 1, "добыча": 1, "есть": 2}


@dataclass(frozen=True)
class StepNeed:
    name: str
    required: tuple[tuple[str, ...], ...]   # группы классов: для группы достаточно одного класса
    useful: tuple[str, ...]
    if_missing: str                          # заменитель, если обязательного класса нет


# Р2: на шагах 6, 9 и 10 обязателен класс главной метрики цикла; для цели-продаж шаг 9 требует и класс D. Класс
# факта-основания известен только у гипотезы — его проверяет `check_hypothesis_in_route()` при добавлении гипотезы.
STEP_CLASSES = {
    0: StepNeed("обнаружение источников", (), CLASSES, "«нет данных» и список дыр"),
    1: StepNeed("оспорить цель", (("D",),), ("C",),
                "грубый расчёт юнит-экономики на доступных данных со статусом «оценка»"),
    2: StepNeed("диагноз", (("A", "C"), ("D",)), ("B", "F"), "лиды как proxy, модель помечается лидовой"),
    3: StepNeed("узкое место", (("C",), ("D",)), ("A", "B", "F"), "понижение уверенности, разбор зависших сделок"),
    4: StepNeed("сегменты и работы", (("F",), ("D",)), ("A",),
                "экспертная оценка со статусом «оценка»; сегмент остаётся гипотезой для поля"),
    5: StepNeed("механики", (), ("D",), ""),
    6: StepNeed("гипотезы", ((MAIN,),), ("F",), "без факта — research, деньги не считаются"),
    7: StepNeed("оценка и приоритет", (("D",), ("A", "C")), ("B",),
                "качественная оценка до расчёта, в реестр не попадает"),
    8: StepNeed("RAT", (("F",),), ("D", "B", "E"), "дешёвые пробы, «ухудшающий эксперимент» по цене"),
    9: StepNeed("тест и замер", ((MAIN,),), ("F", "E"), "мало трафика — качественная проверка вместо A/B"),
    10: StepNeed("вывод и память", ((MAIN,), ("D",)), ("B", "C", "F"), "решение research"),
}


@dataclass(frozen=True)
class StartingRoute:
    start: str
    leads_to: str
    extra: dict               # шаг → классы, которые задача добавляет к полезным


# Библиотека стартовых маршрутов канона: отправные точки, а не сценарии.
BUSINESS_TASKS = {
    "объём лидов": StartingRoute(
        "разделить вход на потоки (веб / без визита / офлайн); проверить, не смешана ли метрика; спрос и сезон",
        "трафик по сегментам, конверсия по страницам до блока, атрибуция ручных сделок", {2: ("E",), 3: ("A", "E")}),
    "конверсия в покупку": StartingRoute(
        "(1−конверсия): кто не купил и почему — диалоги первыми", "барьеры, язык выгоды, ABCDX по платящим",
        {2: ("F",), 3: ("F",)}),
    "средний чек / маржа": StartingRoute(
        "ABCDX платящей базы → работы A/B-сегмента → упаковка оффера", "сегментация цены, «ухудшающий эксперимент»",
        {3: ("D",), 4: ("D",)}),
    "экономика канала": StartingRoute(
        "CPU против AMPU по каналу, не blended", "порог цены лида, отсечение убыточного", {2: ("B",), 3: ("B",)}),
    "возвращаемость": StartingRoute(
        "сначала проверка: есть ли вторая продажа вообще", "если нет — маршрут закрывается выводом «не рычаг»",
        {1: ("D",)}),
}
RETENTION_TASK = "возвращаемость"
CLOSED_REASON = "маршрут закрыт на шаге 1 выводом «не рычаг»: покупка разовая — второй продажи нет"

# Признаки формы бизнеса, которые меняют маршрут в v1.
FORM_KEYS = ("purchase", "ticket", "manual_share", "deal_cycle")
FORM_NOTES = {
    ("purchase", "разовая"): "разовая покупка: когорты — только как цикл сделки (лид → первая оплата); удержание и "
                             "LTV не строим",
    ("purchase", "повторная"): "повторная покупка: когортный треугольник обязателен",
    ("ticket", "высокий"): "высокий чек: рычаги — чек, маржа и конверсия в покупателя; A/B по продажам недостижимы "
                           "по объёму — RAT и «ухудшающий эксперимент»",
    ("manual_share", "высокая"): "доля ручных сделок высокая: атрибуция ручных сделок — первый рычаг",
    ("deal_cycle", "длинный"): "длинный цикл сделки: недельный ритм — только на быстрых метриках; деньги приходят "
                               "с лагом — отложенный замер",
}
SALES_SUBSTITUTES = {
    ("ticket", "высокий"): "продажи в тесте: RAT и «ухудшающий эксперимент» вместо A/B — объёма не хватит",
    ("deal_cycle", "длинный"): "деньги теста — отложенным замером после цикла сделки",
}
CONFIDENCE_NOTE = ("уверенность выведена из статусов классов карты источников, а не из глубины данных: ловушки карты "
                   "сверяются на каждом шаге")


def required_classes(step: int, main_class: str, sales_goal: bool = False) -> tuple[tuple[str, ...], ...]:
    """Обязательные группы классов шага по Р2; класс главной метрики подставляется из маршрута, для цели-продаж шаг 9
    требует и класс D («D для продаж»)."""
    need = STEP_CLASSES.get(step)
    if need is None:
        return ()
    groups = tuple(tuple(main_class if cls == MAIN else cls for cls in group) for group in need.required)
    if sales_goal and step == 9 and ("D",) not in groups:
        groups += (("D",),)
    return groups


def derive_confidence(steps, main_class: str, closed_at: int = 0, sales_goal: bool = False) -> str:
    """Уверенность вывода — по худшему обязательному классу: все «есть» — высокая; заменитель или добыча — средняя;
    «нет данных» — низкая. Пропуск шага с обязательными классами считается как «нет данных», кроме шагов после
    закрытия маршрута выводом. Маршрут без шагов с классами — низкая."""
    ranks = []
    for s in steps:
        groups = required_classes(s.step, main_class, sales_goal)
        if not s.classes:
            if groups and not (closed_at and s.step > closed_at):
                ranks.append(_DECISION_RANK["нет данных"])
            continue
        for group in groups:
            covered = [_DECISION_RANK[s.classes[cls]] for cls in group if s.classes.get(cls) in _DECISION_RANK]
            if covered:
                ranks.append(max(covered))
    return CONFIDENCE[min(ranks)] if ranks else CONFIDENCE[0]


def _form(business_form: dict) -> dict:
    for key in FORM_KEYS:
        if business_form.get(key) not in BUSINESS_FORM[key]:
            raise GuardViolation(9, f"форма бизнеса: «{key}» = {business_form.get(key)!r}, допустимо "
                                    f"{sorted(BUSINESS_FORM[key])}")
    return business_form


def route_draft(cycle_id: str, goal: str, business_task: str, business_form: dict, class_status: dict,
                main_class: str, written_on: date, sales_goal: bool) -> CycleRoute:
    """Черновик маршрута цикла до исполнения. Владелец согласует и правит; отклонение записывается с причиной.
    Продажи ли цель цикла — объявляется явно: для цели-продаж шаг 9 требует класс D."""
    if not isinstance(sales_goal, bool):
        raise GuardViolation(9, f"не объявлено, продажи ли цель цикла: {sales_goal!r} — нужно да или нет")
    if business_task not in BUSINESS_TASKS:
        raise GuardViolation(9, f"бизнес-задача «{business_task}» не из библиотеки: {', '.join(BUSINESS_TASKS)}")
    if main_class not in CLASSES:
        raise GuardViolation(9, f"класс главной метрики «{main_class}» не из A–F")
    form = _form(business_form)
    task = BUSINESS_TASKS[business_task]
    connected = {cls for cls, status in class_status.items() if status == CONNECTED}
    closed = business_task == RETENTION_TASK and form["purchase"] == "разовая"
    notes = [f"бизнес-задача «{business_task}»: начинаем с — {task.start}; обычно ведёт к — {task.leads_to}"]
    notes += [FORM_NOTES[(key, form[key])] for key in FORM_KEYS if (key, form[key]) in FORM_NOTES]
    if sales_goal:
        notes.append("цель — продажи: на шаге 9 обязателен класс D («D для продаж», Р2)")
    steps = []
    for number in CYCLE_STEPS:
        need = STEP_CLASSES[number]
        if closed and number > 1:
            steps.append(RouteStep(step=number, classes={}, skipped_reason=CLOSED_REASON))
            continue
        classes, substitutes = {}, []
        for group in required_classes(number, main_class, sales_goal):
            present = [cls for cls in group if cls in connected]
            for cls in present or group:
                classes[cls] = "есть" if present else "заменитель"
            if not present and need.if_missing not in substitutes:
                substitutes.append(need.if_missing)
        for cls in need.useful + tuple(task.extra.get(number, ())):
            if cls in connected and cls not in classes:
                classes[cls] = "есть"
        if number == 9:
            sales = [text for (key, value), text in SALES_SUBSTITUTES.items() if form[key] == value]
            if sales:
                classes["D"] = "заменитель"
                substitutes += sales
        skipped = "" if classes else f"шаг «{need.name}»: обязательных классов нет, полезные не подключены"
        steps.append(RouteStep(step=number, classes=classes, substitute="; ".join(substitutes), skipped_reason=skipped))
    if closed:
        notes.append(CLOSED_REASON)
    notes.append(CONFIDENCE_NOTE)
    closed_at = 1 if closed else 0
    route = CycleRoute(cycle_id=cycle_id, goal=goal, written_on=written_on,
                       expected_confidence=derive_confidence(steps, main_class, closed_at, sales_goal),
                       steps=tuple(steps), main_class=main_class, notes=tuple(notes), closed_at=closed_at,
                       closed_reason=CLOSED_REASON if closed else "", sales_goal=sales_goal)
    validate_route(route, class_status)
    return route


def check_hypothesis_in_route(route: CycleRoute, hypothesis) -> None:
    """Р2, шаг 6: класс факта-основания известен только у гипотезы — гипотеза встаёт в цикл, если маршрут перечисляет
    этот класс на шаге 6 с любым решением (есть, заменитель, добыча, нет данных). Иначе — страж 9."""
    if hypothesis.cycle_id != route.cycle_id:
        raise GuardViolation(9, f"гипотеза {hypothesis.id} из цикла «{hypothesis.cycle_id}», а маршрут — "
                                f"«{route.cycle_id}»")
    if hypothesis.fact_basis is None:
        raise GuardViolation(6, f"гипотеза {hypothesis.id}: нет факта-основания")
    fact_class = hypothesis.fact_basis.source_class
    step6 = next((s for s in route.steps if s.step == 6), None)
    if step6 is None or fact_class not in step6.classes:
        raise GuardViolation(9, f"гипотеза {hypothesis.id}: класс факта-основания {fact_class} не перечислен на шаге 6 "
                                f"маршрута {route.cycle_id}")
