"""Общая логика книги-таблицы для контракта хранилища (задача 5.5): пять примитивов над любым мостом с командами
tabs · read · write · addtab · insert.

Правила из проб Lark 15.09.2026, которым следует и мост Google: новый лист — 200 строк × 20 колонок; чтение и запись
только внутри листа; вставка кладёт строки и колонки ПЕРЕД позицией, а за последней строкой вставить нельзя, поэтому
последняя строка и последняя колонка листа держатся пустыми и место добавляется вставкой перед ними — данные выше и
левее не сдвигаются; запись — не больше 20 диапазонов и 20 000 ячеек за вызов. Пишутся только непрерывные отрезки
колонок движка — колонки людей между ними не задеваются; колонка без имени с данными людей под колонку движка не
занимается.

Размеры листов перечитываются перед каждым полным чтением листа и после своих вставок — строки, добавленные руками
во время команды, не теряются. Вынесено из бэкенда Lark 28.09.2026: логика одна на оба моста, а общий код пакета не
должен носить имя поставщика.
"""
from __future__ import annotations

import re

from ..core.errors import GuardViolation

MAX_RANGES = 20
MAX_CELLS = 20_000
GROW_ROWS = 200
GROW_COLUMNS = 10
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-.]{20,}")
_EMAIL = re.compile(r"\S+@\S+")
_QUERY = re.compile(r"\?[^\s\"'<>)]+")


def column_letter(index: int) -> str:
    """Номер колонки → буквы: 1 → A, 26 → Z, 27 → AA."""
    if index < 1:
        raise GuardViolation(13, f"номер колонки {index} — нужен больше нуля")
    letters = ""
    while index > 0:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def redact(text: str) -> str:
    """Текст ошибки моста для сообщения: без строки запроса ссылок, адресов почты и длинных строк, похожих на токены."""
    return _LONG_TOKEN.sub("…", _EMAIL.sub("…", _QUERY.sub("?…", text)))


def _empty(cell) -> bool:
    return cell in ("", None)


def _header(cells: list) -> list[str]:
    cells = list(cells)
    while cells and _empty(cells[-1]):
        cells.pop()
    return ["" if _empty(cell) else str(cell) for cell in cells]


def _data_rows(header: list[str], grid: list[list]) -> list[list]:
    """Строки данных под шапкой в её ширину, без пустого хвоста листа; пустые строки внутри сохраняют позиции."""
    rows = [row[:len(header)] for row in grid[1:]]
    while rows and all(_empty(cell) for cell in rows[-1]):
        rows.pop()
    return rows


class TableBackend:
    """Пять примитивов бэкенда-таблицы над книгой; лист находится по имени, диапазоны — по номеру листа. Мост —
    любой с командами tabs · read · write · addtab · insert."""

    def __init__(self, book: str, bridge):
        self.book, self.bridge, self._tabs_cache = book, bridge, None

    # --- листы и границы ---

    def _tabs(self, refresh: bool = False) -> dict:
        if refresh or self._tabs_cache is None:
            result = self.bridge.call("tabs", book=self.book)
            self._tabs_cache = {tab["title"]: tab for tab in result.get("tabs", [])}
        return self._tabs_cache

    def _tab(self, sheet: str, refresh: bool = False) -> dict:
        tab = self._tabs(refresh).get(sheet)
        if tab is None:
            raise GuardViolation(13, f"листа «{sheet}» нет в книге")
        if not isinstance(tab.get("row_count"), int) or not isinstance(tab.get("column_count"), int):
            raise GuardViolation(13, f"мост не вернул размеры листа «{sheet}» — границы чтения неизвестны",
                                 GuardViolation.COVERAGE)
        return tab

    def _read(self, tab: dict, first_row: int, last_row: int, width: int) -> list[list]:
        """Строки листа в ширину `width`, порциями не больше 20 000 ячеек; пустая ячейка — пустая строка."""
        rows, step, start = [], max(1, MAX_CELLS // width), first_row
        while start <= last_row:
            end = min(last_row, start + step - 1)
            result = self.bridge.call("read", book=self.book,
                                      ranges=[f"{tab['sheet_id']}!A{start}:{column_letter(width)}{end}"])
            ranges = result.get("value_ranges")
            if not isinstance(ranges, list) or len(ranges) != 1:
                raise GuardViolation(13, f"мост вернул чтение «{tab['title']}» без диапазона значений — пустым не "
                                         "считается", GuardViolation.COVERAGE)
            values = list(ranges[0].get("values") or [])[:end - start + 1]
            values += [[] for _ in range(end - start + 1 - len(values))]
            rows += [["" if cell is None else cell for cell in list(row)[:width]] + [""] * (width - len(row))
                     for row in values]
            start = end + 1
        return rows

    def _load(self, sheet: str) -> tuple[dict, list[str], list[list]]:
        """Весь лист одним чтением (или порциями): размеры, шапка и сетка значений."""
        tab = self._tab(sheet, refresh=True)
        grid = self._read(tab, 1, tab["row_count"], tab["column_count"])
        return tab, _header(grid[0]), grid

    def _ensure_width(self, sheet: str, width: int) -> dict:
        """Последняя колонка листа остаётся пустой: колонки вставляются перед ней."""
        tab = self._tab(sheet)
        if width > tab["column_count"] - 1:
            self.bridge.call("insert", book=self.book, sheet=tab["sheet_id"],
                             position=column_letter(tab["column_count"]),
                             count=width - tab["column_count"] + 1 + GROW_COLUMNS)
            tab = self._tabs(refresh=True)[sheet]
        return tab

    def _ensure_rows(self, sheet: str, last_needed_row: int) -> dict:
        """Последняя строка листа остаётся пустой: строки вставляются перед ней (за последней вставить нельзя)."""
        tab = self._tab(sheet)
        if last_needed_row > tab["row_count"] - 1:
            self.bridge.call("insert", book=self.book, sheet=tab["sheet_id"], position=str(tab["row_count"]),
                             count=last_needed_row - tab["row_count"] + 1 + GROW_ROWS)
            tab = self._tabs(refresh=True)[sheet]
        return tab

    def _write_blocks(self, blocks: list) -> None:
        """blocks — [номер листа, первая колонка, последняя колонка, первая строка, матрица]; порции ≤ 20 и ≤ 20 000."""
        pieces = []
        for sheet_id, first_col, last_col, first_row, matrix in blocks:
            width = last_col - first_col + 1
            step = max(1, MAX_CELLS // width)
            for offset in range(0, len(matrix), step):
                part, row = matrix[offset:offset + step], first_row + offset
                pieces.append((f"{sheet_id}!{column_letter(first_col)}{row}:{column_letter(last_col)}"
                               f"{row + len(part) - 1}", part, len(part) * width))
        batch, cells = [], 0
        for range_text, values, size in pieces:
            if batch and (len(batch) == MAX_RANGES or cells + size > MAX_CELLS):
                self.bridge.call("write", book=self.book, value_ranges=batch)
                batch, cells = [], 0
            batch.append({"range": range_text, "values": values})
            cells += size
        if batch:
            self.bridge.call("write", book=self.book, value_ranges=batch)

    # --- пять примитивов ---

    def header(self, sheet: str) -> list[str] | None:
        if sheet not in self._tabs() and sheet not in self._tabs(refresh=True):
            return None
        tab = self._tab(sheet)
        return _header(self._read(tab, 1, 1, tab["column_count"])[0])

    def create_sheet(self, sheet: str, columns: list[str]) -> None:
        if sheet in self._tabs(refresh=True):
            raise GuardViolation(13, f"лист «{sheet}» уже есть — второй лист с тем же именем не создаётся")
        self.bridge.call("addtab", book=self.book, title=sheet)
        self._tabs(refresh=True)
        if columns:
            tab = self._ensure_width(sheet, len(columns))
            self._write_blocks([(tab["sheet_id"], 1, len(columns), 1, [list(columns)])])

    def add_columns(self, sheet: str, columns: list[str]) -> None:
        _, header, grid = self._load(sheet)
        extra = [column for column in columns if column not in header]
        if not extra:
            return
        first, last = len(header) + 1, len(header) + len(extra)
        taken = sorted({index for row in grid[1:] for index in range(first, min(last, len(row)) + 1)
                        if not _empty(row[index - 1])})
        if taken:
            raise GuardViolation(13, f"«{sheet}»: в колонках без имени {', '.join(map(column_letter, taken))} есть "
                                     "данные — дать колонке имя или освободить её; колонки движка не дописаны")
        tab = self._ensure_width(sheet, last)
        self._write_blocks([(tab["sheet_id"], first, last, 1, [extra])])

    def read_rows(self, sheet: str) -> list[dict]:
        _, header, grid = self._load(sheet)
        return [dict(zip(header, row)) for row in _data_rows(header, grid)]

    def write_rows(self, sheet: str, items: list) -> None:
        _, header, grid = self._load(sheet)
        existing = len(_data_rows(header, grid))
        next_position, targets = existing, []
        for position, values in items:
            unknown = [column for column in values if column not in header]
            if unknown:
                raise GuardViolation(13, f"«{sheet}»: колонок {', '.join(unknown)} нет в шапке")
            if position is None:
                position, next_position = next_position, next_position + 1
            elif not 0 <= position < existing:
                raise GuardViolation(13, f"«{sheet}»: строки данных №{position + 1} нет — обновлять нечего")
            if values:
                targets.append((position + 2, values))
        if not targets:
            return
        tab = self._ensure_rows(sheet, max(row for row, _ in targets))
        blocks, open_blocks = [], {}
        for row, values in targets:
            indexes = sorted(header.index(column) + 1 for column in values)
            runs = [[indexes[0]]]
            for index in indexes[1:]:
                if index == runs[-1][-1] + 1:
                    runs[-1].append(index)
                else:
                    runs.append([index])
            for run in runs:
                cells, key = [values[header[index - 1]] for index in run], (run[0], run[-1])
                block = open_blocks.get(key)
                if block is not None and block[3] + len(block[4]) == row:
                    block[4].append(cells)
                else:
                    open_blocks[key] = [tab["sheet_id"], run[0], run[-1], row, [cells]]
                    blocks.append(open_blocks[key])
        self._write_blocks(blocks)
