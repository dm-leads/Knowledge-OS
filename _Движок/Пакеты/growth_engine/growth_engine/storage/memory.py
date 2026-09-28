"""Бэкенд-таблица в памяти: эталон поведения пяти примитивов для контрактных тестов хранилища и отладки.

Лист — шапка и строки-словари; строки не удаляются; запись в колонку, которой нет в шапке, — стоп.
"""
from __future__ import annotations

from ..core.errors import GuardViolation


class MemoryBackend:
    def __init__(self):
        self.sheets = {}          # имя листа → {"header": [...], "rows": [{колонка: текст}]}

    def header(self, sheet: str) -> list[str] | None:
        found = self.sheets.get(sheet)
        return None if found is None else list(found["header"])

    def create_sheet(self, sheet: str, columns: list[str]) -> None:
        if sheet in self.sheets:
            raise GuardViolation(13, f"лист «{sheet}» уже есть — второй лист с тем же именем не создаётся")
        self.sheets[sheet] = {"header": list(columns), "rows": []}

    def add_columns(self, sheet: str, columns: list[str]) -> None:
        header = self.sheets[sheet]["header"]
        header.extend(column for column in columns if column not in header)

    def read_rows(self, sheet: str) -> list[dict]:
        found = self.sheets[sheet]
        return [{column: row.get(column, "") for column in found["header"]} for row in found["rows"]]

    def write_rows(self, sheet: str, items: list) -> None:
        found = self.sheets[sheet]
        for position, values in items:
            unknown = [column for column in values if column not in found["header"]]
            if unknown:
                raise GuardViolation(13, f"«{sheet}»: колонок {', '.join(unknown)} нет в шапке")
            if position is None:
                found["rows"].append(dict(values))
            else:
                found["rows"][position].update(values)
