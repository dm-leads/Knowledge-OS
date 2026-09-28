"""Адаптер хранилища «файл Markdown»: аварийный режим и тестовая замена хранилища реестра.

Числа пишутся таблицей со всеми полями канонического числа и сразу читаются обратно; расхождение — ошибка.
Бэкенд-таблица контракта хранилища — `MarkdownBackend` ниже; `MarkdownStore` читает снимки этапов 2–3.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..core.errors import GuardViolation
from ..core.number import Number, Status
from ..core.storage import WriteReport

COLUMNS = ("metric", "level", "scope", "flow", "segment", "period_start", "period_end", "as_of", "source", "status",
           "value", "denominator", "unit", "missing")
_PIPE = "&#124;"


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Status):
        return value.value
    if isinstance(value, float):
        return repr(value)
    return str(value).replace("|", _PIPE).replace("\n", " ")


def _parse(cells) -> Number:
    raw = dict(zip(COLUMNS, (cell.replace(_PIPE, "|") for cell in cells)))
    return Number(metric=raw["metric"], level=raw["level"], scope=raw["scope"], flow=raw["flow"],
                  segment=raw["segment"],
                  period_start=date.fromisoformat(raw["period_start"]),
                  period_end=date.fromisoformat(raw["period_end"]),
                  as_of=date.fromisoformat(raw["as_of"]),
                  source=raw["source"], status=Status(raw["status"]),
                  value=float(raw["value"]) if raw["value"] != "" else None,
                  denominator=raw["denominator"] or None, unit=raw["unit"], missing=raw["missing"])


class MarkdownStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, artifact: str) -> Path:
        return self.directory / f"{artifact}.md"

    def _render(self, artifact: str, items) -> str:
        lines = [f"# {artifact}", "", "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
        lines += ["| " + " | ".join(_cell(getattr(item, column)) for column in COLUMNS) + " |" for item in items]
        return "\n".join(lines) + "\n"

    def write_numbers(self, artifact: str, numbers) -> WriteReport:
        numbers = list(numbers)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(artifact).write_text(self._render(artifact, numbers), encoding="utf-8")
        back = self.read_numbers(artifact)
        if back != numbers:
            raise GuardViolation(13, f"«{artifact}»: прочитанное не совпало с записанным "
                                     f"({len(numbers)} записано, {len(back)} прочитано)")
        return WriteReport(artifact, len(numbers), len(back))

    def read_numbers(self, artifact: str) -> list[Number]:
        rows = []
        for line in self._path(artifact).read_text(encoding="utf-8").splitlines()[4:]:
            if line.startswith("| "):
                rows.append(_parse([cell.strip() for cell in line[2:-2].split(" | ")]))
        return rows


# --- бэкенд-таблица контракта хранилища (задача 5.4) ---

_ENTITIES = (("&", "&amp;"), ("|", "&#124;"), ("\n", "&#10;"), ("\r", "&#13;"))


def _escape_cell(text: str) -> str:
    for raw, entity in _ENTITIES:
        text = text.replace(raw, entity)
    return text


def _unescape_cell(text: str) -> str:
    for raw, entity in reversed(_ENTITIES):
        text = text.replace(entity, raw)
    return text


class MarkdownBackend:
    """Бэкенд-таблица «файл Markdown» для контракта хранилища (Х1): лист — файл `<лист>.md` с таблицей. Ячейки без
    потерь: `&`, `|` и переводы строк экранируются HTML-сущностями, пробелы по краям сохраняются. Аварийный и офлайн
    режим; формул нет. Файл пишется целиком через временный файл — строки людей и колонки справа остаются."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, sheet: str) -> Path:
        return self.directory / f"{sheet}.md"

    @staticmethod
    def _cells(line: str) -> list[str]:
        inner = line[1:-1] if line.endswith("|") else line[1:]
        return [_unescape_cell(cell[1:-1] if len(cell) >= 2 and cell.startswith(" ") and cell.endswith(" ") else cell)
                for cell in inner.split("|")]

    @staticmethod
    def _line(values) -> str:
        return "|" + "|".join(f" {_escape_cell(value)} " for value in values) + "|"

    def _load(self, sheet: str):
        path = self._path(sheet)
        if not path.exists():
            return None
        table = [line for line in path.read_text(encoding="utf-8").split("\n") if line.startswith("|")]
        if len(table) < 2:
            raise GuardViolation(13, f"«{sheet}»: в файле нет таблицы с шапкой")
        header, rows = self._cells(table[0]), [self._cells(line) for line in table[2:]]
        for position, cells in enumerate(rows, start=1):
            if len(cells) != len(header):
                raise GuardViolation(13, f"«{sheet}»: строка {position} таблицы шириной {len(cells)}, а шапка — "
                                         f"{len(header)}: файл изменён вне движка")
        return header, rows

    def _save(self, sheet: str, header: list[str], rows: list[list[str]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        lines = [f"# {sheet}", "", self._line(header), "|" + "|".join("---" for _ in header) + "|"]
        lines += [self._line(row) for row in rows]
        path = self._path(sheet)
        temporary = path.parent / (path.name + ".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(path)

    def header(self, sheet: str) -> list[str] | None:
        loaded = self._load(sheet)
        return None if loaded is None else list(loaded[0])

    def create_sheet(self, sheet: str, columns: list[str]) -> None:
        if self._path(sheet).exists():
            raise GuardViolation(13, f"лист «{sheet}» уже есть — второй файл с тем же именем не создаётся")
        self._save(sheet, list(columns), [])

    def add_columns(self, sheet: str, columns: list[str]) -> None:
        header, rows = self._load(sheet)
        extra = [column for column in columns if column not in header]
        self._save(sheet, header + extra, [row + [""] * len(extra) for row in rows])

    def read_rows(self, sheet: str) -> list[dict]:
        header, rows = self._load(sheet)
        return [dict(zip(header, row)) for row in rows]

    def write_rows(self, sheet: str, items: list) -> None:
        header, rows = self._load(sheet)
        for position, values in items:
            unknown = [column for column in values if column not in header]
            if unknown:
                raise GuardViolation(13, f"«{sheet}»: колонок {', '.join(unknown)} нет в шапке")
            if position is None:
                rows.append([values.get(column, "") for column in header])
            else:
                for column, value in values.items():
                    rows[position][header.index(column)] = value
        self._save(sheet, header, rows)
