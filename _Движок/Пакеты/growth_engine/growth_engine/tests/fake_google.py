"""Модель Google Sheets API для тестов хранилища — поведение, на которое опирается мост.

Чтение отдаёт значения без пустого хвоста строк и колонок, а пустой диапазон — без ключа `values`; диапазон за
пределами листа — ошибка 400 «exceeds grid limits». Запись в режиме RAW хранит строки как есть, в режиме USER_ENTERED
строка с «=» становится формулой, «TRUE»/«FALSE» — логическим: так тест ловит запись не в том режиме. Лист задаётся в
диапазоне именем в кавычках, в запросах структуры — номером. Ошибки — настоящие `gspread.exceptions.APIError`.
"""
import re

from gspread.exceptions import APIError

_RANGE = re.compile(r"^'((?:[^']|'')+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)$")


class _Response:
    def __init__(self, code: int, message: str, status: str):
        self.status_code, self._body = code, {"error": {"code": code, "message": message, "status": status}}
        self.text = message

    def json(self):
        return self._body


def api_error(code: int, message: str, status: str = "INVALID_ARGUMENT") -> APIError:
    return APIError(_Response(code, message, status))


def _column_number(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {}            # номер листа → {"title", "grid": [[ячейка]], "hidden"}
        self.calls = []             # (метод, подробность)
        self.fail_with = None       # ошибка, которой ответят следующие обращения
        self._next_gid = 100

    def _check(self, method, detail=None):
        self.calls.append((method, detail))
        if self.fail_with is not None:
            raise self.fail_with

    def add(self, title: str, rows: int = 1000, columns: int = 26) -> int:
        gid, self._next_gid = self._next_gid, self._next_gid + 1
        self.sheets[gid] = {"title": title, "grid": [[None] * columns for _ in range(rows)], "hidden": False}
        return gid

    def _by_title(self, title: str) -> dict:
        for sheet in self.sheets.values():
            if sheet["title"] == title:
                return sheet
        raise api_error(400, f"Unable to parse range: '{title}'")

    def _cells(self, text: str):
        match = _RANGE.match(text)
        if not match:
            raise api_error(400, f"Unable to parse range: {text}")
        title, first_col, first_row, last_col, last_row = match.groups()
        grid = self._by_title(title.replace("''", "'"))["grid"]
        first_col, last_col, first_row, last_row = (_column_number(first_col), _column_number(last_col),
                                                    int(first_row), int(last_row))
        if last_row > len(grid) or last_col > len(grid[0]):
            raise api_error(400, f"Range ('{title}'!{text.split('!')[1]}) exceeds grid limits.")
        return grid, first_col, first_row, last_col, last_row

    # --- четыре метода gspread.Spreadsheet, которыми пользуется мост ---

    def fetch_sheet_metadata(self, params=None):
        self._check("metadata")
        return {"sheets": [{"properties": {"sheetId": gid, "title": sheet["title"], "hidden": sheet["hidden"],
                                           "gridProperties": {"rowCount": len(sheet["grid"]),
                                                              "columnCount": len(sheet["grid"][0])}}}
                           for gid, sheet in self.sheets.items()]}

    def values_batch_get(self, ranges, params=None):
        self._check("read", len(ranges))
        result = []
        for text in ranges:
            grid, first_col, first_row, last_col, last_row = self._cells(text)
            rows = [[cell for cell in row[first_col - 1:last_col]] for row in grid[first_row - 1:last_row]]
            trimmed = []
            for row in rows:
                while row and row[-1] in (None, ""):
                    row = row[:-1]
                trimmed.append(row)
            while trimmed and not trimmed[-1]:
                trimmed.pop()
            entry = {"range": text, "majorDimension": "ROWS"}
            if trimmed:
                entry["values"] = trimmed
            result.append(entry)
        return {"valueRanges": result}

    @staticmethod
    def _entered(value, mode: str):
        if mode == "RAW" or not isinstance(value, str):
            return value
        if value.startswith("="):
            return "ВЫЧИСЛЕНО"
        if value.strip().upper() in ("TRUE", "FALSE"):
            return value.strip().upper() == "TRUE"
        return value

    def values_batch_update(self, body):
        self._check("write", len(body.get("data", [])))
        mode = body.get("valueInputOption")
        if mode not in ("RAW", "USER_ENTERED"):
            raise api_error(400, "Invalid valueInputOption")
        cells = 0
        for entry in body["data"]:
            grid, first_col, first_row, last_col, last_row = self._cells(entry["range"])
            values = entry["values"]
            if len(values) > last_row - first_row + 1 or any(len(row) > last_col - first_col + 1 for row in values):
                raise api_error(400, "Requested writing within range, but tried writing outside it")
            for r, row in enumerate(values):
                for c, value in enumerate(row):
                    grid[first_row - 1 + r][first_col - 1 + c] = None if value == "" else self._entered(value, mode)
                    cells += 1
        return {"totalUpdatedCells": cells}

    def batch_update(self, body):
        replies = []
        for request in body["requests"]:
            (kind, spec), = request.items()
            self._check(kind)
            if kind == "addSheet":
                props = spec["properties"]
                if any(sheet["title"] == props["title"] for sheet in self.sheets.values()):
                    raise api_error(400, f"A sheet with the name \"{props['title']}\" already exists.")
                grid = props.get("gridProperties", {})
                gid = self.add(props["title"], grid.get("rowCount", 1000), grid.get("columnCount", 26))
                replies.append({"addSheet": {"properties": {"sheetId": gid, "title": props["title"]}}})
            elif kind == "insertDimension":
                rng = spec["range"]
                grid = self.sheets[rng["sheetId"]]["grid"]
                start, count = rng["startIndex"], rng["endIndex"] - rng["startIndex"]
                if rng["dimension"] == "ROWS":
                    if start > len(grid):
                        raise api_error(400, "startIndex beyond the grid")
                    for _ in range(count):
                        grid.insert(start, [None] * len(grid[0]))
                else:
                    if start > len(grid[0]):
                        raise api_error(400, "startIndex beyond the grid")
                    for row in grid:
                        row[start:start] = [None] * count
                replies.append({})
            elif kind == "updateSheetProperties":
                props = spec["properties"]
                if spec.get("fields") != "hidden":
                    raise api_error(400, "only the hidden field is modelled")
                self.sheets[props["sheetId"]]["hidden"] = props["hidden"]
                replies.append({})
            else:
                raise api_error(400, f"request {kind} is not modelled")
        return {"replies": replies}
