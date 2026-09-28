"""Лестницы глубины A–F (Стандарт — Протокол обнаружения источников, §4) и единица действия.

Гипотеза формулируется только на единице действия: без координат нижней ступени класса
она стоит на уровне метрики и отвергается (страж 6).
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import GuardViolation

LADDERS = {
    "A": ("канал", "тип страницы", "URL", "блок / скролл / клик / событие", "шаг формы или квиза", "устройство"),
    "B": ("канал", "кампания", "группа", "ключ / креатив", "посадочная", "устройство / регион"),
    "C": ("компания", "канал", "поток", "страница входа", "сделка", "этап остановки"),
    "D": ("компания", "сегмент", "продукт", "сделка", "менеджер", "задача после разговора"),
    "E": ("категория", "куст запросов", "запрос", "позиция × URL", "сезон помесячно"),
    "F": ("корпус", "сегмент / контур", "этап разговора", "паттерн обрыва", "диалог с цитатой"),
}

# Координаты нижней ступени каждого класса.
ACTION_COORDINATES = {
    "A": ("url", "element"),                      # блок, CTA или шаг формы на конкретной странице
    "B": ("keyword", "landing", "device"),        # ключ × посадочная × устройство
    "C": ("stage", "flow", "source"),             # этап × поток × источник
    "D": ("segment", "product", "manager"),       # сегмент × продукт × менеджер
    "E": ("query", "url", "position"),            # запрос × наша страница × позиция
    "F": ("stage", "pattern", "examples_n", "base_n"),  # паттерн с числом примеров и базой
}

MIN_PATTERN_EXAMPLES = 20


@dataclass(frozen=True)
class UnitOfAction:
    source_class: str
    coordinates: dict

    def __post_init__(self):
        if self.source_class not in ACTION_COORDINATES:
            raise GuardViolation(6, f"класс источника «{self.source_class}» не из A–F")
        required = ACTION_COORDINATES[self.source_class]
        missing = [k for k in required if not str(self.coordinates.get(k, "")).strip()]
        if missing:
            raise GuardViolation(
                6, f"единица действия класса {self.source_class} выше нижней ступени: нет {', '.join(missing)}")
        if self.source_class == "F":
            examples, base = int(self.coordinates["examples_n"]), int(self.coordinates["base_n"])
            if examples < MIN_PATTERN_EXAMPLES:
                raise GuardViolation(6, f"паттерн на {examples} примерах: порог канона ≥{MIN_PATTERN_EXAMPLES}")
            if base < examples:
                raise GuardViolation(6, "база меньше числа примеров — знаменатель неверен")
