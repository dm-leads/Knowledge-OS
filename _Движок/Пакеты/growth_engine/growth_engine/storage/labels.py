"""Русские заголовки листов книги: человек читает «Гипотеза», движок работает с `formulation`.

Просьба владельца 24.09.2026: латинские шапки рядом с русскими листами читаются как чужая история. Движок находит
колонки по именам, поэтому подписи — словарь «имя → подпись», а `LabelledBackend` переводит в обе стороны: новые листы
и новые колонки получают подписи, строки читаются и пишутся по именам движка. Лист со старой латинской шапкой читается
и пишется как раньше: обёртка смотрит, в какой форме колонка уже стоит в шапке, и сама ничего не переписывает.
Колонки людей без подписи проходят как есть.

Подписи уникальны внутри каждого листа — это держит тест; иначе две колонки движка слились бы в одну.
"""
from __future__ import annotations

LABELS = {
    # общие
    "id": "№", "revision": "Ревизия", "updated_at": "Изменено", "status": "Статус", "name": "Название",
    "source": "Источник", "unit_of_action": "Где меняем", "cycle_id": "Цикл",
    # Карта источников
    "source_class": "Класс источника", "history_from": "История с", "truth_point": "Точка истины", "probe": "Проба",
    "traps": "Ловушки", "secret_env_names": "Переменные секретов",
    # Модель — снимки
    "metric": "Метрика", "level": "Уровень", "scope": "Кабинет", "flow": "Поток", "segment": "Разрез",
    "period_start": "Период с", "period_end": "Период по", "as_of": "Снято", "value": "Значение",
    "denominator": "Знаменатель", "unit": "Единица", "missing": "Чего не хватает",
    # Дерево цели
    "goal_id": "Цель (№ числа)", "branch_id": "Ветка", "ceiling_id": "Потолок ветки (№ числа)",
    "hypothesis_ids": "Гипотезы ветки", "no_hypotheses_reason": "Почему гипотез нет",
    # Маршрут цикла
    "goal": "Цель", "written_on": "Записан", "expected_confidence": "Ожидаемая уверенность",
    "main_class": "Главный класс данных", "notes": "Заметки", "closed_at": "Закрыт", "closed_reason": "Причина закрытия",
    "sales_goal": "Цель — продажи", "step": "Шаг", "classes": "Классы данных", "substitute": "Заменитель",
    "extraction_cost": "Цена добычи", "skipped_reason": "Почему пропущен", "no_data_reason": "Почему нет данных",
    # Гипотезы
    "formulation": "Гипотеза", "version": "Версия", "supersedes": "Заменяет", "business_task": "Бизнес-задача",
    "tree_branch": "Ветка дерева", "model_lever": "Рычаг", "fact_basis_id": "Факт-основание (№ числа)",
    "effect_goal_units_id": "Эффект в единицах цели (№ числа)", "effect_rub_id": "Эффект в ₽ (№ числа)",
    "mechanic": "Механика", "main_metric": "Метрика проверки", "owner": "Ответственный", "zone": "Зона",
    "change": "Изменение", "start_date": "Старт", "window_days": "Окно, дней", "expected_n": "Ожидаемый объём",
    "threshold": "Порог шума", "expected_delta": "Ожидаемый эффект", "expected_kind": "Вид эффекта",
    "noise_share_id": "Доля для порога (№ числа)", "noise_threshold_id": "Порог шума (№ числа)",
    "failure_criterion": "Критерий провала", "rat": "Допущения (RAT)", "deferred_until": "Отложена до",
    "blocked_class": "Заблокирована классом", "status_reason": "Причина статуса", "measured_id": "Замер (№ числа)",
    "in_threshold": "Эффект выше шума", "decision": "Решение", "conclusion": "Вывод", "knowledge_row": "Строка знаний",
    # Карта знаний
    "statement": "Утверждение", "verdict": "Вердикт", "on": "Дата", "hypothesis_id": "Гипотеза (№)",
    # Журнал решений
    "decided": "Что решили", "why": "Почему", "subtraction": "Что убрали", "alternatives": "Альтернативы",
}
NAMES = {label: name for name, label in LABELS.items()}


class LabelledBackend:
    """Бэкенд-таблица с русскими заголовками поверх любого бэкенда из пяти примитивов."""

    def __init__(self, inner):
        self.inner = inner

    def _placed(self, sheet: str) -> dict[str, str]:
        """Имя движка → как колонка записана в шапке (подписью или латиницей)."""
        placed = {}
        for column in self.inner.header(sheet) or []:
            placed.setdefault(NAMES.get(column, column), column)
        return placed

    def header(self, sheet: str) -> list[str] | None:
        raw = self.inner.header(sheet)
        return None if raw is None else [NAMES.get(column, column) for column in raw]

    def create_sheet(self, sheet: str, columns: list[str]) -> None:
        self.inner.create_sheet(sheet, [LABELS.get(column, column) for column in columns])

    def add_columns(self, sheet: str, columns: list[str]) -> None:
        raw = self.inner.header(sheet) or []
        # Лист в латинице (собран до подписей) дописывается латиницей: одна шапка — один язык.
        latin = any(column in LABELS for column in raw) and not any(column in NAMES for column in raw)
        placed = self._placed(sheet)
        extra = [column if latin else LABELS.get(column, column) for column in columns if column not in placed]
        if extra:
            self.inner.add_columns(sheet, extra)

    def read_rows(self, sheet: str) -> list[dict]:
        return [{NAMES.get(column, column): value for column, value in row.items()}
                for row in self.inner.read_rows(sheet)]

    def write_rows(self, sheet: str, items: list) -> None:
        placed = self._placed(sheet)
        self.inner.write_rows(sheet, [(position, {placed.get(column, LABELS.get(column, column)): value
                                                  for column, value in values.items()})
                                      for position, values in items])
