"""Мост к Google Sheets с командами Lark-моста — tabs · read · write · addtab · insert, и ещё hide.

Хранилище переехало в Google 24.09.2026, когда у Lark кончилась месячная квота вызовов API. Логика таблицы общая с
Lark (`TableBackend` в `storage/table.py`: шапка, рост листа, порции записи, колонки людей), поэтому мост отвечает теми же формами, что и
Lark-мост, а отличия Google прячет в себе:

- лист в диапазоне у Google задаётся именем в кавычках, а бэкенд пишет номер листа — мост переводит номер в имя;
- пустой диапазон Google отдаёт без значений — мост возвращает пустой список, а не пропуск;
- запись идёт в режиме RAW: строка с «=» остаётся текстом, «TRUE» — строкой, как рассчитывает кодек ячеек;
- минутный лимит запросов (429) и сбой сервиса (5xx) — временные: пауза и повтор; месячной квоты у Google нет;
- отказ ключа — нехватка доступа (покрытие), а не «пусто»; текст ошибок — без адресов почты и длинных токенов.

Ключ — OAuth-токен пользователя, тот же, что у сервера Google Sheets (MCP): файлы принадлежат владельцу таблицы.
Путь — переменная GOOGLE_SHEETS_TOKEN_PATH, иначе ~/.config/mcp-google-sheets/token.json; значения ключа не печатаются.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..core.errors import GuardViolation
from .table import redact

NEW_ROWS, NEW_COLUMNS = 200, 20            # как у нового листа Lark: бэкенду одинаково, где расти
RETRY_PAUSES = (20, 60)                    # секунды; минутный лимит Google сбрасывается за минуту
TEMPORARY_CODES = (429, 500, 502, 503)
TOKEN_ENV = "GOOGLE_SHEETS_TOKEN_PATH"
DEFAULT_TOKEN = Path("~/.config/mcp-google-sheets/token.json")


def token_path() -> Path:
    return Path(os.environ.get(TOKEN_ENV) or DEFAULT_TOKEN).expanduser()


def open_with_user_token(book: str):
    """Книга по ключу через gspread с OAuth-токеном пользователя; токен обновляется в памяти, файл не переписывается."""
    import gspread
    from google.oauth2.credentials import Credentials

    path = token_path()
    if not path.is_file():
        raise GuardViolation(13, f"нет ключа Google: файла токена нет по пути из {TOKEN_ENV} — нужна авторизация "
                                 "сервера Google Sheets", GuardViolation.COVERAGE)
    credentials = Credentials.from_authorized_user_info(json.loads(path.read_text(encoding="utf-8")))
    return gspread.authorize(credentials).open_by_key(book)


def _column_number(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value


def _quoted(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


class GoogleBridge:
    """Команды Lark-моста поверх Google Sheets API; `open_book` и `sleep` подменяются в тестах."""

    def __init__(self, open_book=open_with_user_token, sleep=time.sleep):
        self._open, self._sleep = open_book, sleep
        self._books, self._titles = {}, {}

    def __enter__(self) -> "GoogleBridge":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Процесса нет — закрывать нечего; соединения gspread закрываются сами."""

    # --- обращения к API с разбором отказов ---

    def _api(self, action):
        from google.auth.exceptions import RefreshError, TransportError
        from gspread.exceptions import APIError

        for attempt in range(len(RETRY_PAUSES) + 1):
            try:
                return action()
            except RefreshError:
                raise GuardViolation(13, "нет доступа к Google: токен пользователя недействителен или отозван — нужна "
                                         "повторная авторизация; данные не подставляются",
                                     GuardViolation.COVERAGE) from None
            except TransportError:
                raise GuardViolation(13, "нет связи с Google — данные не подставляются",
                                     GuardViolation.COVERAGE) from None
            except APIError as error:
                code = getattr(error, "code", None)
                if code in TEMPORARY_CODES and attempt < len(RETRY_PAUSES):
                    self._sleep(RETRY_PAUSES[attempt])
                    continue
                if code in TEMPORARY_CODES:
                    raise GuardViolation(13, f"Google: лимит запросов или сбой сервиса ({code}) не прошёл после "
                                             f"{len(RETRY_PAUSES)} пауз — повторить позже",
                                         GuardViolation.COVERAGE) from None
                raise GuardViolation(13, f"Google Sheets: {redact(str(error))}") from None
        raise AssertionError("недостижимо")

    def _book(self, book: str):
        if book not in self._books:
            self._books[book] = self._api(lambda: self._open(book))
        return self._books[book]

    def _metadata(self, book: str) -> list[dict]:
        sheets = self._api(lambda: self._book(book).fetch_sheet_metadata()).get("sheets", [])
        props = [sheet["properties"] for sheet in sheets]
        self._titles[book] = {str(p["sheetId"]): p["title"] for p in props}
        return props

    def _range(self, book: str, text: str) -> str:
        """«номер!A1:B2» → «'Имя листа'!A1:B2»."""
        sheet_id, _, cells = text.partition("!")
        titles = self._titles.get(book) or {}
        if sheet_id not in titles:
            self._metadata(book)
            titles = self._titles[book]
        if sheet_id not in titles:
            raise GuardViolation(13, f"листа с номером {sheet_id} нет в книге")
        return f"{_quoted(titles[sheet_id])}!{cells}"

    # --- команды ---

    def call(self, command: str, **args) -> dict:
        handler = getattr(self, f"_cmd_{command}", None)
        if handler is None:
            raise GuardViolation(13, f"мост Google: команды «{command}» нет")
        return handler(**args)

    def _cmd_tabs(self, book: str) -> dict:
        return {"tabs": [{"sheet_id": str(p["sheetId"]), "title": p["title"],
                          "row_count": p["gridProperties"]["rowCount"],
                          "column_count": p["gridProperties"]["columnCount"],
                          "hidden": bool(p.get("hidden", False))} for p in self._metadata(book)]}

    def _cmd_read(self, book: str, ranges: list[str]) -> dict:
        translated = [self._range(book, text) for text in ranges]
        reply = self._api(lambda: self._book(book).values_batch_get(
            translated, params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "FORMATTED_STRING"}))
        found = reply.get("valueRanges", [])
        if len(found) != len(ranges):
            raise GuardViolation(13, "Google вернул не столько диапазонов, сколько запрошено — пустым не считается",
                                 GuardViolation.COVERAGE)
        return {"value_ranges": [{"range": text, "values": entry.get("values", [])}
                                 for text, entry in zip(ranges, found)]}

    def _cmd_write(self, book: str, value_ranges: list[dict], dry_run: bool = False) -> dict:
        data = [{"range": self._range(book, entry["range"]), "values": entry["values"]} for entry in value_ranges]
        cells = sum(len(row) for entry in value_ranges for row in entry["values"])
        if not dry_run:
            self._api(lambda: self._book(book).values_batch_update({"valueInputOption": "RAW", "data": data}))
        return {"cells": cells}

    def _cmd_addtab(self, book: str, title: str, index: int | None = None) -> dict:
        props = {"title": title, "gridProperties": {"rowCount": NEW_ROWS, "columnCount": NEW_COLUMNS}}
        if index is not None:
            props["index"] = index
        reply = self._api(lambda: self._book(book).batch_update({"requests": [{"addSheet": {"properties": props}}]}))
        sheet_id = str(reply["replies"][0]["addSheet"]["properties"]["sheetId"])
        self._titles.setdefault(book, {})[sheet_id] = title
        return {"sheet_id": sheet_id}

    def _cmd_insert(self, book: str, sheet: str, position: str, count: int) -> dict:
        if position.isdigit():
            dimension, start = "ROWS", int(position) - 1
        else:
            dimension, start = "COLUMNS", _column_number(position) - 1
        request = {"insertDimension": {"range": {"sheetId": int(sheet), "dimension": dimension, "startIndex": start,
                                                 "endIndex": start + count}, "inheritFromBefore": False}}
        self._api(lambda: self._book(book).batch_update({"requests": [request]}))
        return {}

    def _cmd_hide(self, book: str, sheet: str) -> dict:
        request = {"updateSheetProperties": {"properties": {"sheetId": int(sheet), "hidden": True}, "fields": "hidden"}}
        self._api(lambda: self._book(book).batch_update({"requests": [request]}))
        return {}
