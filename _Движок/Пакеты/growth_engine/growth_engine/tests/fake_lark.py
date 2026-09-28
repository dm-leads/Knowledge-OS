"""Модель моста Lark для тестов хранилища — поведение, проверенное пробами 15.09.2026.

Новый лист — 200 строк × 20 колонок; чтение и запись за пределами листа — ошибка; вставка кладёт строки и колонки перед
позицией, за последней строкой и колонкой вставить нельзя; не больше 20 диапазонов и 20 000 ячеек за запись; «=…»
становится формулой (читается вычисленным значением), true/false — логическим («TRUE» / «FALSE»); ведущий апостроф
сохраняется. Пустая ячейка читается как null — строже живого сервера, чтобы бэкенд выдерживал оба ответа.
"""
import re

from growth_engine.storage.lark_sheets import MAX_CELLS, MAX_RANGES, bridge_error

NEW_ROWS, NEW_COLUMNS = 200, 20
# Невидимые пробелы, которые живой сервис заменяет обычными при записи.
INVISIBLE_SPACES = re.compile("[   ⁠]")
_RANGE = re.compile(r"^([^!]+)!([A-Z]+)(\d+):([A-Z]+)(\d+)$")


def column_number(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value


class FakeLarkBridge:
    def __init__(self):
        self.sheets = {}           # номер листа → {"title", "grid": [[ячейка]]}
        self.calls = []            # (команда, число диапазонов записи)
        self.fail_with = None      # текст ошибки, которой мост ответит на следующие команды

    def call(self, command: str, **args) -> dict:
        self.calls.append((command, len(args.get("value_ranges", []))))
        if self.fail_with:
            raise bridge_error(self.fail_with)
        return getattr(self, f"_{command}")(**args)

    def close(self) -> None:
        """У модели нет процесса — закрывать нечего."""

    def _tabs(self, book):
        return {"tabs": [{"sheet_id": sheet_id, "title": sheet["title"], "row_count": len(sheet["grid"]),
                          "column_count": len(sheet["grid"][0])} for sheet_id, sheet in self.sheets.items()]}

    def _addtab(self, book, title):
        if any(sheet["title"] == title for sheet in self.sheets.values()):
            raise bridge_error(f"sheet title {title} already exists")
        sheet_id = f"s{len(self.sheets) + 1}"
        self.sheets[sheet_id] = {"title": title, "grid": [[None] * NEW_COLUMNS for _ in range(NEW_ROWS)]}
        return {"sheet_id": sheet_id}

    def _cells(self, text):
        match = _RANGE.match(text)
        if not match:
            raise bridge_error(f"invalid range {text}")
        sheet_id, first_col, first_row, last_col, last_row = match.groups()
        grid = self.sheets[sheet_id]["grid"]
        first_col, last_col, first_row, last_row = (column_number(first_col), column_number(last_col),
                                                    int(first_row), int(last_row))
        if last_row > len(grid) or last_col > len(grid[0]):
            raise bridge_error("Request failed with status code 400")
        return grid, first_col, first_row, last_col, last_row

    def _read(self, book, ranges):
        if len(ranges) > MAX_RANGES:
            raise bridge_error("too many ranges")
        result = []
        for text in ranges:
            grid, first_col, first_row, last_col, last_row = self._cells(text)
            result.append({"range": text, "values": [row[first_col - 1:last_col]
                                                     for row in grid[first_row - 1:last_row]]})
        return {"value_ranges": result}

    @staticmethod
    def _coerce(value):
        if value == "":
            return None
        if isinstance(value, str) and value.startswith("="):
            return "ВЫЧИСЛЕНО"
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().upper()
        if isinstance(value, str):
            # Живой сервис молча заменяет невидимые пробелы обычными — проверено первым живым циклом 17.09.2026,
            # когда названия разрезов с U+00A0 перестали сходиться с номерами своих чисел. Прежняя модель этого не
            # делала, поэтому случай не ловился ни одним тестом.
            return INVISIBLE_SPACES.sub(" ", value)
        return value

    def _write(self, book, value_ranges, dry_run=False):
        if len(value_ranges) > MAX_RANGES:
            raise bridge_error(f"value_ranges: от 1 до {MAX_RANGES} диапазонов")
        cells = sum(len(entry["values"]) * len(entry["values"][0]) for entry in value_ranges)
        if cells > MAX_CELLS:
            raise bridge_error(f"value_ranges: {cells} ячеек, предел {MAX_CELLS}")
        for entry in value_ranges:
            grid, first_col, first_row, last_col, last_row = self._cells(entry["range"])
            values = entry["values"]
            if len(values) != last_row - first_row + 1 or any(len(row) != last_col - first_col + 1 for row in values):
                raise bridge_error("values do not match the declared range")
            for r, row in enumerate(values):
                for c, value in enumerate(row):
                    grid[first_row - 1 + r][first_col - 1 + c] = self._coerce(value)
        return {"cells": cells}

    def _insert(self, book, sheet, position, count):
        grid = self.sheets[sheet]["grid"]
        if position.isdigit():
            index = int(position)
            if index > len(grid):
                raise bridge_error(f"invalid insert position: row {index} is outside the sheet's row range")
            for _ in range(count):
                grid.insert(index - 1, [None] * len(grid[0]))
        else:
            index = column_number(position)
            if index > len(grid[0]):
                raise bridge_error(f"invalid insert position: column {position} is outside the sheet")
            for row in grid:
                row[index - 1:index - 1] = [None] * count
        return {"row_count": len(grid), "column_count": len(grid[0])}
