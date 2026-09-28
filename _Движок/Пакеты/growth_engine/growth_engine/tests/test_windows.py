"""Окна месяцев (28.09.2026): чистая функция над датами переехала в ядро — общие команды не тянут адаптер поставщика."""
from datetime import date

from growth_engine.core.windows import month_windows


def test_windows_cross_the_year_with_exclusive_right_edge():
    assert month_windows("2026-11", 3) == [(date(2026, 11, 1), date(2026, 12, 1)),
                                           (date(2026, 12, 1), date(2027, 1, 1)),
                                           (date(2027, 1, 1), date(2027, 2, 1))]
