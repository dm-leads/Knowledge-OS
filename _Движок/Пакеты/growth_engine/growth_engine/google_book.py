"""Сборка рабочей книги Движка роста в Google Sheets: три листа для человека, служебное движка скрыто.

Решения владельца 24.09.2026. Рабочие листы — «Гипотезы» (реестр ведёт движок), «Воронка и оценка гипотез» и
«Юнит-экономика» (копии из исходной таблицы вместе с формулами). Служебные листы движка лежат в той же книге и скрыты;
у «Гипотез» рабочие колонки слева, служебные (номера чисел, ревизии, состояние) скрыты.

Сборка идемпотентна: план — чистая функция от состояния книги, повторный запуск на собранной книге пуст. Без ключа
`--apply` печатается только план. Успех — повторный план пуст, а не слово «готово».

    py -3 -m growth_engine.google_book --config <конфигурация> --parent <папка Drive> --source <исходная книга> [--apply]
"""
from __future__ import annotations

import argparse
import sys

import yaml

from .core.errors import GuardViolation
from .core.storage import COLUMNS, SHEETS, SYSTEM_COLUMNS
from .storage.labels import LABELS, NAMES, LabelledBackend

FOLDER_NAME = "Движок роста"
COPIED = ("Юнит-экономика", "Воронка и оценка гипотез")   # порядок важен: «Воронка» ссылается на «Юнит-экономику»
WORKING_ORDER = [SHEETS["hypotheses"], "Воронка и оценка гипотез", "Юнит-экономика"]
# Что человек читает в строке гипотезы. Номера чисел (`*_id`) — ссылки на лист снимков, человеку они ничего не говорят.
HYPOTHESIS_VISIBLE = ("id", "formulation", "status", "model_lever", "unit_of_action", "main_metric", "mechanic",
                      "owner", "start_date", "window_days", "threshold", "expected_delta", "in_threshold", "decision",
                      "conclusion")
# Пометки к рабочим колонкам «Гипотез» — всплывают при наведении на заголовок.
HYPOTHESIS_NOTES = {
    "id": "Номер гипотезы. Присваивает движок при заведении.",
    "formulation": "Если [что меняем], то [метрика] изменится на [сколько], потому что [факт с источником].",
    "status": "идея → research → candidate → в тесте → замер → вывод. Меняется только через движок: он не пустит "
              "в тест без порога шума и окна.",
    "model_lever": "Какой рычаг юнит-экономики двигаем: трафик, конверсия, средний чек, маржа.",
    "unit_of_action": "Что именно меняем: страница, раздел, канал или шаг воронки. Должна совпадать с узким местом, "
                      "принятым на шаге 3.",
    "main_metric": "Метрика, по которой меряем результат теста.",
    "mechanic": "Как именно меняем — что делаем руками.",
    "owner": "Кто отвечает за запуск и замер.",
    "start_date": "Дата запуска теста.",
    "window_days": "Сколько дней идёт тест. Замер — не раньше конца окна.",
    "threshold": "Порог шума в долях (0,003 = 0,3 п.п.). Эффект меньше порога неотличим от случайности.",
    "expected_delta": "Ожидаемое изменение метрики — объявляется до запуска, а не подбирается после.",
    "in_threshold": "После замера: изменение больше порога шума (да) или нет.",
    "decision": "scale — масштабировать; iterate — новая версия; kill — убрать; research — дособрать данные.",
    "conclusion": "Что узнали о рынке — одной фразой. Уходит в карту знаний.",
}
# Оформление — как у соседних листов книги: Trebuchet MS, шапка жирная на светло-голубом фоне, перенос строк.
HEADER_FILL = {"red": 0.8117647, "green": 0.8862745, "blue": 0.9529412}
FONT = "Trebuchet MS"
WIDTHS = {"id": 70, "formulation": 420, "status": 100, "model_lever": 110, "unit_of_action": 200, "main_metric": 130,
          "mechanic": 240, "owner": 120, "start_date": 95, "window_days": 75, "threshold": 90, "expected_delta": 110,
          "in_threshold": 95, "decision": 100, "conclusion": 320}
# Колонки людей на «Гипотезах» (их пишет task_brief): ширина ставится, когда колонка появилась.
PEOPLE_WIDTHS = {"Исполнитель": 180, "ТЗ исполнителю": 480, "Как это работает": 360}
FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"


def hypothesis_header() -> list[str]:
    """Шапка «Гипотез»: рабочие колонки слева, дальше остальные колонки движка в их порядке."""
    engine = list(COLUMNS["hypotheses"]) + list(SYSTEM_COLUMNS)
    return list(HYPOTHESIS_VISIBLE) + [column for column in engine if column not in HYPOTHESIS_VISIBLE]


def engine_header(title: str) -> list[str]:
    if title == SHEETS["hypotheses"]:
        return hypothesis_header()
    kind = next(kind for kind, sheet in SHEETS.items() if sheet == title)
    return list(COLUMNS[kind]) + list(SYSTEM_COLUMNS)


def ref_errors(grid: list[list]) -> int:
    """Сколько ячеек показывают #REF! — ссылка формулы на лист, которого в книге нет."""
    return sum(1 for row in grid for cell in row if isinstance(cell, str) and "#REF!" in cell)


def plan(state: dict) -> list[tuple]:
    """Действия, которые приводят книгу к сборке. state: folder, book, sheets{имя: index, hidden, header, empty,
    hidden_columns (начало, конец) или None, frozen_rows}."""
    actions: list[tuple] = []
    if state["folder"] is None:
        actions.append(("create_folder", FOLDER_NAME))
    if state["book"] is None:
        actions.append(("create_book",))
    sheets = state["sheets"]
    hypotheses = SHEETS["hypotheses"]
    existing = sheets.get(hypotheses)
    if existing and existing["header"] and "id" not in existing["header"]:
        return [("stop", f"«{hypotheses}»: лист есть, но это не лист движка (нет колонки id)")]

    actions += [("copy", title) for title in COPIED if title not in sheets]
    actions += [("create_sheet", title, engine_header(title)) for title in SHEETS.values() if title not in sheets]
    for kind, title in SHEETS.items():
        if kind != "hypotheses" and not (sheets.get(title) or {}).get("hidden"):
            actions.append(("hide_sheet", title))

    header = existing["header"] if existing else hypothesis_header()
    # Прячутся только колонки движка: колонки людей (исполнитель, ТЗ) дописываются в конец шапки и должны быть видны.
    engine = set(hypothesis_header())
    engine_end = max((i + 1 for i, column in enumerate(header) if column in engine), default=len(header))
    wanted = (len(HYPOTHESIS_VISIBLE), engine_end)
    hidden = (existing or {}).get("hidden_columns")
    # Прятать по положению можно, только если рабочие колонки действительно стоят слева.
    if tuple(header[:len(HYPOTHESIS_VISIBLE)]) == HYPOTHESIS_VISIBLE and hidden != wanted:
        if hidden and hidden[0] == wanted[0] and hidden[1] > wanted[1]:
            actions.append(("show_columns", hypotheses, wanted[1], hidden[1]))
        else:
            actions.append(("hide_columns", hypotheses, *wanted))
    if (existing or {}).get("frozen_rows") != 1:
        actions.append(("freeze", hypotheses))
    if not (existing or {}).get("styled"):
        actions.append(("style", hypotheses))
    if not (existing or {}).get("notes"):
        actions.append(("notes", hypotheses))
    if existing:
        widths = existing.get("widths") or {}
        wrong = {column: width for column, width in PEOPLE_WIDTHS.items()
                 if column in existing["header"] and widths.get(column) != width}
        if wrong:
            actions.append(("widen", hypotheses, wrong))
    for title in SHEETS.values():
        sheet = sheets.get(title)
        if sheet and not sheet.get("labelled", True):
            raw = sheet.get("raw_header") or sheet["header"]
            actions.append(("relabel", title, [LABELS.get(column, column) for column in raw]))

    known = set(SHEETS.values()) | set(COPIED)
    actions += [("delete_sheet", title) for title, sheet in sheets.items() if title not in known and sheet["empty"]]
    if [sheets.get(title, {}).get("index") for title in WORKING_ORDER] != list(range(len(WORKING_ORDER))):
        actions.append(("order", WORKING_ORDER))
    return actions


# --- исполнение поверх Google API ---

class Workbench:
    """Папка, книга и листы через gspread и Drive API с OAuth-токеном пользователя (как у Google-моста)."""

    def __init__(self, client):
        self.client = client

    @classmethod
    def with_user_token(cls) -> "Workbench":
        import gspread
        from google.oauth2.credentials import Credentials
        import json

        from .storage.google_sheets import token_path

        path = token_path()
        if not path.is_file():
            raise GuardViolation(13, "нет ключа Google: файла токена нет — нужна авторизация сервера Google Sheets",
                                 GuardViolation.COVERAGE)
        creds = Credentials.from_authorized_user_info(json.loads(path.read_text(encoding="utf-8")))
        # Минутный лимит чтения Google (429) — временный: клиент с паузой и повтором, как у моста (сборка 24.09.2026
        # упала без него на проверке #REF!).
        from gspread.http_client import BackOffHTTPClient
        return cls(gspread.authorize(creds, http_client=BackOffHTTPClient))

    def _drive(self, method: str, params=None, json=None) -> dict:
        response = self.client.http_client.request(method, DRIVE_FILES, params=params, json=json)
        return response.json()

    def find(self, parent: str, name: str, mime: str) -> str | None:
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        found = self._drive("get", params={"q": f"'{parent}' in parents and name = '{escaped}' and mimeType = '{mime}' "
                                                "and trashed = false", "fields": "files(id,name)"}).get("files", [])
        if len(found) > 1:
            raise GuardViolation(13, f"в папке два объекта «{name}» — какой брать, неизвестно")
        return found[0]["id"] if found else None

    def create_folder(self, parent: str, name: str) -> str:
        return self._drive("post", params={"fields": "id"},
                           json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]})["id"]

    def create_book(self, folder: str, title: str) -> str:
        return self.client.create(title, folder_id=folder).id

    def state(self, folder: str | None, book: str | None) -> dict:
        state = {"folder": folder, "book": book, "sheets": {}}
        if book is None:
            return state
        spreadsheet = self.client.open_by_key(book)
        # Диапазон без имени листа («1:1») Google относит к первому листу и возвращает только его — поэтому свойства
        # всех листов берутся отдельно, а скрытые колонки читаются только у «Гипотез», где они нужны (найдено
        # первой живой сборкой 24.09.2026).
        sheets = spreadsheet.fetch_sheet_metadata(params={"fields": "sheets.properties"})["sheets"]
        titles = [sheet["properties"]["title"] for sheet in sheets]
        quoted = {t: "'" + t.replace("'", "''") + "'" for t in titles}
        tops = spreadsheet.values_batch_get([f"{quoted[t]}!A1:AZ3" for t in titles]).get("valueRanges", [])
        columns, cells = [], []
        if SHEETS["hypotheses"] in quoted:
            data = spreadsheet.fetch_sheet_metadata(params={
                "fields": "sheets(data(columnMetadata(hiddenByUser,pixelSize),rowData(values(note,userEnteredFormat(textFormat("
                          "bold,fontFamily))))))",
                "ranges": f"{quoted[SHEETS['hypotheses']]}!1:1"})["sheets"][0].get("data") or [{}]
            columns = data[0].get("columnMetadata", [])
            cells = (data[0].get("rowData") or [{}])[0].get("values", [])
        for sheet, top in zip(sheets, tops):
            props = sheet["properties"]
            values = top.get("values", [])
            raw = [str(cell) for cell in (values[0] if values else [])]
            header = [NAMES.get(column, column) for column in raw]
            hidden = ([i for i, column in enumerate(columns) if column.get("hiddenByUser")]
                      if props["title"] == SHEETS["hypotheses"] else [])
            state["sheets"][props["title"]] = {
                "sheet_id": props["sheetId"], "index": props["index"], "hidden": bool(props.get("hidden")),
                "header": header, "raw_header": raw, "empty": not values,
                "labelled": not any(column in LABELS and column not in NAMES for column in raw),
                "hidden_columns": (hidden[0], hidden[-1] + 1) if hidden else None,
                "frozen_rows": props.get("gridProperties", {}).get("frozenRowCount", 0)}
            if props["title"] == SHEETS["hypotheses"]:
                first = (cells[0].get("userEnteredFormat") or {}).get("textFormat", {}) if cells else {}
                state["sheets"][props["title"]]["styled"] = bool(first.get("bold")) and first.get("fontFamily") == FONT
                visible = len(HYPOTHESIS_VISIBLE)
                state["sheets"][props["title"]]["notes"] = (len(cells) >= visible
                                                             and all(cell.get("note") for cell in cells[:visible]))
                state["sheets"][props["title"]]["widths"] = {
                    column: (columns[i].get("pixelSize") if i < len(columns) else None)
                    for i, column in enumerate(header) if column in PEOPLE_WIDTHS}
        return state

    def apply(self, actions: list[tuple], folder: str | None, book: str | None, parent: str, title: str,
              source: str, out) -> tuple[str, str]:
        from .storage.google_sheets import GoogleBridge
        from .storage.table import TableBackend

        for action in actions:
            kind = action[0]
            if kind == "stop":
                raise GuardViolation(13, action[1])
            if kind == "create_folder":
                folder = self.create_folder(parent, action[1])
                out(f"создана папка «{action[1]}»")
            elif kind == "create_book":
                book = self.create_book(folder, title)
                out(f"создана книга «{title}»")
            elif kind == "copy":
                target = self.client.open_by_key(book)
                copied = self.client.open_by_key(source).worksheet(action[1]).copy_to(book)
                target.batch_update({"requests": [{"updateSheetProperties": {
                    "properties": {"sheetId": copied["sheetId"], "title": action[1]}, "fields": "title"}}]})
                out(f"скопирован лист «{action[1]}» с формулами")
            elif kind == "relabel":
                quoted = "'" + action[1].replace("'", "''") + "'"
                self.client.open_by_key(book).values_batch_update({"valueInputOption": "RAW", "data": [
                    {"range": f"{quoted}!A1", "values": [action[2]]}]})
                out(f"«{action[1]}»: шапка переписана по-русски, колонок {len(action[2])}")
            elif kind == "create_sheet":
                LabelledBackend(TableBackend(book, GoogleBridge(
                    open_book=lambda key: self.client.open_by_key(key)))).create_sheet(
                    action[1], action[2])
                out(f"создан лист «{action[1]}»: колонок {len(action[2])}")
            else:
                current = self.state(folder, book)["sheets"]
                request = self._request(action, current)
                self.client.open_by_key(book).batch_update({"requests": request})
                out(f"{self._label(action)}")
        return folder, book

    @staticmethod
    def _request(action: tuple, sheets: dict) -> list[dict]:
        kind = action[0]
        if kind == "hide_sheet":
            return [{"updateSheetProperties": {"properties": {"sheetId": sheets[action[1]]["sheet_id"], "hidden": True},
                                               "fields": "hidden"}}]
        if kind == "hide_columns":
            return [{"updateDimensionProperties": {
                "range": {"sheetId": sheets[action[1]]["sheet_id"], "dimension": "COLUMNS", "startIndex": action[2],
                          "endIndex": action[3]}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}}]
        if kind == "show_columns":
            return [{"updateDimensionProperties": {
                "range": {"sheetId": sheets[action[1]]["sheet_id"], "dimension": "COLUMNS", "startIndex": action[2],
                          "endIndex": action[3]}, "properties": {"hiddenByUser": False}, "fields": "hiddenByUser"}}]
        if kind == "freeze":
            return [{"updateSheetProperties": {"properties": {"sheetId": sheets[action[1]]["sheet_id"],
                                                              "gridProperties": {"frozenRowCount": 1}},
                                               "fields": "gridProperties.frozenRowCount"}}]
        if kind == "style":
            sheet_id, header = sheets[action[1]]["sheet_id"], sheets[action[1]]["header"]
            requests = [
                {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                                "cell": {"userEnteredFormat": {
                                    "backgroundColor": HEADER_FILL, "wrapStrategy": "WRAP",
                                    "verticalAlignment": "MIDDLE",
                                    "textFormat": {"bold": True, "fontFamily": FONT, "fontSize": 10}}},
                                "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,"
                                          "verticalAlignment)"}},
                {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 1},
                                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP",
                                                               "textFormat": {"fontFamily": FONT, "fontSize": 10}}},
                                "fields": "userEnteredFormat(textFormat,wrapStrategy,verticalAlignment)"}},
                {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0,
                                                         "endIndex": 1},
                                               "properties": {"pixelSize": 44}, "fields": "pixelSize"}}]
            for index, column in enumerate(header[:len(HYPOTHESIS_VISIBLE)]):
                requests.append({"updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1},
                    "properties": {"pixelSize": WIDTHS.get(column, 110)}, "fields": "pixelSize"}})
            return requests
        if kind == "notes":
            sheet_id, header = sheets[action[1]]["sheet_id"], sheets[action[1]]["header"]
            values = [{"note": HYPOTHESIS_NOTES.get(column, "")} for column in header[:len(HYPOTHESIS_VISIBLE)]]
            return [{"updateCells": {"rows": [{"values": values}], "fields": "note",
                                     "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0}}}]
        if kind == "widen":
            sheet_id, header = sheets[action[1]]["sheet_id"], sheets[action[1]]["header"]
            return [{"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": header.index(column),
                          "endIndex": header.index(column) + 1},
                "properties": {"pixelSize": width}, "fields": "pixelSize"}} for column, width in action[2].items()]
        if kind == "delete_sheet":
            return [{"deleteSheet": {"sheetId": sheets[action[1]]["sheet_id"]}}]
        if kind == "order":
            return [{"updateSheetProperties": {"properties": {"sheetId": sheets[title]["sheet_id"], "index": index},
                                               "fields": "index"}} for index, title in enumerate(action[1])]
        raise GuardViolation(13, f"сборка книги: действие «{kind}» неизвестно")

    @staticmethod
    def _label(action: tuple) -> str:
        return {"hide_sheet": lambda: f"скрыт лист «{action[1]}»",
                "hide_columns": lambda: f"«{action[1]}»: скрыты служебные колонки {action[2] + 1}–{action[3]}",
                "freeze": lambda: f"«{action[1]}»: закреплена строка шапки",
                "show_columns": lambda: f"«{action[1]}»: снова видны колонки людей {action[2] + 1}–{action[3]}",
                "delete_sheet": lambda: f"удалён пустой лист «{action[1]}»",
                "widen": lambda: f"«{action[1]}»: ширина колонок людей — {', '.join(action[2])}",
                "style": lambda: f"«{action[1]}»: оформление как у соседних листов",
                "notes": lambda: f"«{action[1]}»: пометки к рабочим колонкам — {len(HYPOTHESIS_NOTES)}",
                "order": lambda: "порядок листов: " + ", ".join(action[1])}[action[0]]()


def describe(action: tuple) -> str:
    kind = action[0]
    if kind == "create_sheet":
        return f"создать лист «{action[1]}» ({len(action[2])} колонок)"
    if kind == "hide_columns":
        return f"скрыть в «{action[1]}» колонки {action[2] + 1}–{action[3]}"
    return " ".join(str(part) if not isinstance(part, list) else ", ".join(part) for part in action)


def main(argv=None, out=print) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="growth_engine.google_book", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="конфигурация инстанса (берётся название компании)")
    parser.add_argument("--parent", required=True, help="папка Google Drive, в которой лежит папка «Движок роста»")
    parser.add_argument("--source", required=True, help="исходная книга, откуда копируются листы с формулами")
    parser.add_argument("--apply", action="store_true", help="выполнить план; без ключа — только напечатать")
    args = parser.parse_args(argv)
    try:
        with open(args.config, encoding="utf-8") as handle:
            company = (yaml.safe_load(handle) or {}).get("company") or "инстанс"
        title = f"Движок роста — {company}"
        bench = Workbench.with_user_token()
        folder = bench.find(args.parent, FOLDER_NAME, FOLDER_MIME)
        book = bench.find(folder, title, "application/vnd.google-apps.spreadsheet") if folder else None
        actions = plan(bench.state(folder, book))
        out(f"книга «{title}»: {'есть' if book else 'нет'}; действий в плане: {len(actions)}")
        for action in actions:
            out(f"  — {describe(action)}")
        if not args.apply or not actions:
            out("ИТОГ: " + ("книга собрана, делать нечего" if not actions else "план напечатан, ничего не записано"))
            return 0
        folder, book = bench.apply(actions, folder, book, args.parent, title, args.source, out)
        done, left = len(actions), plan(bench.state(folder, book))
        # Новая книга приходит с пустым листом по умолчанию — его видно только после создания книги; план сходится
        # за один-два дополнительных прохода. Больше — значит, действие не берёт, и это надо показать, а не крутить.
        for _ in range(2):
            if not left or any(action[0] == "stop" for action in left):
                break
            bench.apply(left, folder, book, args.parent, title, args.source, out)
            done, left = done + len(left), plan(bench.state(folder, book))
        actions = [None] * done
        broken = {}
        spreadsheet = bench.client.open_by_key(book)
        for copied in COPIED:
            grid = spreadsheet.worksheet(copied).get_all_values()
            broken[copied] = ref_errors(grid)
        for copied, count in broken.items():
            out(f"«{copied}»: ячеек с #REF! — {count}")
        out(f"ссылка: https://docs.google.com/spreadsheets/d/{book}/edit")
        out(f"ИТОГ: действий выполнено {len(actions)}; повторный план — {len(left)} действий"
            + ("" if not left else ": " + "; ".join(describe(a) for a in left)))
        return 0 if not left and not any(broken.values()) else 1
    except GuardViolation as error:
        out(f"❌ google_book: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
