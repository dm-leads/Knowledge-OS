"""Блок «Воронка по каналам» на листе «Воронка и оценка гипотез»: живые числа движка глазами человека.

Числа берутся с листа снимков (их пишет funnel_run), блок их только показывает — по Красинскому: сколько дошли, конверсия
от первого шага и от прошлого, сколько не прошли; по каналам — где потерян трафик. Три части: воронка по месяцам
(веб-поток), каналы веб-потока (первый месяц против последнего), поток без визита. Над каждой частью — подпись с
кабинетом, потоком, источником, датой съёма и статусом: число без них — не число.

Блок ставится ниже формул владельца и пишет только в свободное место или поверх своего прошлого блока (его первая
ячейка начинается с TITLE). Чужое в области блока — стоп, ничего не записано. Показывается один съём — последний.

Запуск из Скрипты/:
  py -3 -m growth_engine.funnel_sheet --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --google-book <ключ книги> --months 2026-05,2026-06,2026-07,2026-08 [--scope brz] [--start-row 26]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from .core.config import parse_config
from .core.errors import GuardViolation
from .funnel_run import STAGE_NAMES, parse_months
from .storage.selection import add_store_arguments, open_store

TITLE = "Воронка по каналам — живые числа движка"
SHEET = "Воронка и оценка гипотез"
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
HEADER_FILL = {"red": 0.9529412, "green": 0.9529412, "blue": 0.9529412}
TITLE_FILL = {"red": 0.8117647, "green": 0.8862745, "blue": 0.9529412}
FONT = "Trebuchet MS"


def latest_take(numbers) -> date:
    takes = {x.as_of for x in numbers if x.as_of is not None}
    if not takes:
        raise GuardViolation(13, "на листе снимков нет чисел воронки с датой съёма — сначала funnel_run")
    return max(takes)


def _month(start: date) -> str:
    return f"{MONTHS[start.month - 1]} {start:%Y}"


def _label(segment: str) -> str:
    return segment.split("=", 1)[1] if "=" in segment else segment


def _signature(numbers, scope: str, flow: str, take: date) -> str:
    sources = sorted({x.source for x in numbers})
    statuses = sorted({x.status.value for x in numbers})
    flow_name = {"web": "веб-поток", "no_visit": "поток без визита"}.get(flow, flow)
    return (f"кабинет {scope} · {flow_name} · источник {', '.join(sources)} · снято {take:%d.%m.%Y} · "
            f"статус {', '.join(statuses)}")


def build(numbers, stages: list[str], scope: str, months: list[date]) -> tuple[list[list], list[tuple]]:
    """Строки блока и разметка форматов (строка, первая колонка, колонка-за-последней, «count» | «share»)."""
    take = latest_take(numbers)
    chosen = [x for x in numbers if x.as_of == take and x.scope == scope and x.metric in stages]
    index = {(x.metric, x.flow, x.segment, x.period_start): x for x in chosen}

    def value(metric, flow, start, segment=""):
        found = index.get((metric, flow, segment, start))
        return None if found is None else found.value

    for start in months:
        if value(stages[0], "web", start) is None and (stages[0], "web", "", start) not in index:
            raise GuardViolation(13, f"нет чисел воронки за {start:%m.%Y} (кабинет {scope}, съём {take:%d.%m.%Y}) — "
                                     "сначала funnel_run за этот месяц; нулями не заполняется")

    rows: list[list] = [[f"{TITLE} · кабинет {scope}"], []]
    formats: list[tuple] = []

    # 1. Воронка по месяцам, веб-поток.
    web = [x for x in chosen if x.flow == "web" and not x.segment]
    rows.append([f"Воронка по месяцам · {_signature(web, scope, 'web', take)}"])
    header = ["Ступень"]
    for start in months:
        header += [f"{_month(start)}: дошли", "от первого шага", "от прошлого", "не прошли"]
    rows.append(header)
    for position, stage in enumerate(stages):
        row = [STAGE_NAMES.get(stage, stage)]
        for column, start in enumerate(months):
            here = value(stage, "web", start)
            first = value(stages[0], "web", start)
            before = value(stages[position - 1], "web", start) if position else None
            if position == 0 or here is None:
                row += [here if here is not None else "нет данных", "", "", ""]
            else:
                row += [here, here / first if first else "", here / before if before else "",
                        before - here if before is not None else ""]
            base = 1 + column * 4
            formats += [(len(rows), base, base + 1, "count"), (len(rows), base + 1, base + 3, "share"),
                        (len(rows), base + 3, base + 4, "count")]
        rows.append(row)
    rows.append([])

    # 2. Каналы веб-потока: первый месяц против последнего.
    first_month, last_month = months[0], months[-1]
    channels = sorted({x.segment for x in chosen if x.flow == "web" and x.segment},
                      key=lambda segment: -(value(stages[0], "web", first_month, segment) or 0))
    channel_numbers = [x for x in chosen if x.flow == "web" and x.segment]
    rows.append([f"Каналы веб-потока: {_month(first_month)} против {_month(last_month)} · "
                 f"{_signature(channel_numbers, scope, 'web', take)}"])
    later = [STAGE_NAMES.get(stage, stage) for stage in stages[1:]]
    rows.append(["Канал", f"визиты {_month(first_month)}", f"визиты {_month(last_month)}", "изменение",
                 f"{later[0]} {_month(last_month)}", f"визит → {later[0]} {_month(last_month)}"]
                + [f"{name} {_month(last_month)}" for name in later[1:]])
    for segment in channels:
        before, after = value(stages[0], "web", first_month, segment), value(stages[0], "web", last_month, segment)
        second = value(stages[1], "web", last_month, segment)
        row = [_label(segment), before if before is not None else "нет данных",
               after if after is not None else "нет данных",
               (after - before) if before is not None and after is not None else "",
               second if second is not None else "нет данных",
               second / after if second is not None and after else ""]
        row += [value(stage, "web", last_month, segment) for stage in stages[2:]]
        row = [cell if cell is not None else "нет данных" for cell in row]
        formats += [(len(rows), 1, 5, "count"), (len(rows), 5, 6, "share"), (len(rows), 6, len(row), "count")]
        rows.append(row)
    rows.append([])

    # 3. Поток без визита: у него нет визитов по определению потока.
    no_visit = [x for x in chosen if x.flow == "no_visit" and not x.segment]
    rows.append([f"Без визита (сделки из CRM и формы) · {_signature(no_visit, scope, 'no_visit', take)}"])
    rows.append(["Ступень"] + [f"{_month(start)}: дошли" for start in months])
    for stage in stages:
        if stage == "visits":
            continue
        row = [STAGE_NAMES.get(stage, stage)]
        row += [value(stage, "no_visit", start) for start in months]
        formats.append((len(rows), 1, 1 + len(months), "count"))
        rows.append([cell if cell is not None else "нет данных" for cell in row])
    return rows, formats


def free_or_ours(grid: list[list]) -> bool:
    """Область блока свободна или занята нашим прошлым блоком (первая ячейка — заголовок блока)."""
    first = grid[0][0] if grid and grid[0] else ""
    if isinstance(first, str) and first.startswith(TITLE):
        return True
    return not any(cell not in ("", None) for row in grid for cell in row)


def _letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def write(spreadsheet, rows: list[list], formats: list[tuple], start_row: int, out) -> int:
    """Пишет блок с `start_row` (1-я строка = 1); прошлый свой блок стирает целиком; чужое — стоп."""
    worksheet = spreadsheet.worksheet(SHEET)
    width = max(len(row) for row in rows)
    last = _letter(width - 1)
    probe_rows = len(rows) + 80
    existing = worksheet.get(f"A{start_row}:{last}{start_row + probe_rows}", value_render_option="FORMULA")
    existing = [list(row) + [""] * (width - len(row)) for row in existing]
    if not free_or_ours(existing[:len(rows)] or [[""]]):
        raise GuardViolation(13, f"«{SHEET}»: в строках {start_row}–{start_row + len(rows) - 1} лежит чужое — блок не "
                                 "записан; укажите --start-row ниже")
    # Прошлый свой блок мог быть длиннее: он кончается на трёх пустых строках подряд.
    old = 0
    if existing and isinstance(existing[0][0], str) and existing[0][0].startswith(TITLE):
        empty = 0
        for number, row in enumerate(existing, 1):
            empty = empty + 1 if not any(cell not in ("", None) for cell in row) else 0
            if empty == 3:
                old = number - 3
                break
        else:
            old = len(existing)
    height = max(old, len(rows))
    padded = [list(row) + [""] * (width - len(row)) for row in rows]
    padded += [[""] * width for _ in range(height - len(rows))]
    spreadsheet.values_batch_update({"valueInputOption": "RAW", "data": [
        {"range": f"'{SHEET}'!A{start_row}:{last}{start_row + height - 1}", "values": padded}]})

    sheet_id = worksheet.id
    first = start_row - 1
    requests = [{"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": first, "endRowIndex": first + height,
                                          "startColumnIndex": 0, "endColumnIndex": width},
                                "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": FONT, "fontSize": 10}}},
                                "fields": "userEnteredFormat(textFormat,numberFormat,backgroundColor)"}}]
    for offset, row in enumerate(rows):
        label = row[0] if row else ""
        if not isinstance(label, str) or not label:
            continue
        caption = label.startswith(TITLE) or " · " in label
        header = label in ("Ступень", "Канал")
        if caption or header:
            requests.append({"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": first + offset, "endRowIndex": first + offset + 1,
                          "startColumnIndex": 0, "endColumnIndex": width if header else 1},
                "cell": {"userEnteredFormat": {"backgroundColor": TITLE_FILL if caption else HEADER_FILL,
                                               "textFormat": {"bold": True, "fontFamily": FONT, "fontSize": 10}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat)"}})
    patterns = {"count": "#,##0", "share": "0.00%"}
    for offset, col_start, col_end, kind in formats:
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": first + offset, "endRowIndex": first + offset + 1,
                      "startColumnIndex": col_start, "endColumnIndex": col_end},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": patterns[kind]}}},
            "fields": "userEnteredFormat.numberFormat"}})
    spreadsheet.batch_update({"requests": requests})
    out(f"«{SHEET}»: блок записан в строки {start_row}–{start_row + len(rows) - 1}, колонок {width}"
        + (f"; прошлый блок ({old} строк) стёрт" if old else ""))
    return len(rows)


def run(args, out=print, spreadsheet=None) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    funnel = raw.get("funnel") or {}
    stages = list(funnel.get("stages") or [])
    scopes = list(raw["sources"][funnel["system"]]["scopes"]) if funnel.get("system") else []
    scope = args.scope or (scopes[0] if scopes else None)
    if len(stages) < 2 or scope not in scopes:
        raise GuardViolation(9, "нужны раздел funnel в конфигурации и кабинет из его системы")
    months = [start for start, _ in parse_months(args.months)]
    with open_store(args, cfg.storage_link_domains) as (store, label):
        numbers = [x for x in store.read("numbers") if x.metric in stages]
    rows, formats = build(numbers, stages, scope, months)
    if spreadsheet is None:
        from .google_book import Workbench
        spreadsheet = Workbench.with_user_token().client.open_by_key(args.google_book)
    written = write(spreadsheet, rows, formats, args.start_row, out)
    out(f"ИТОГ: строк блока {written}; хранилище: {label}")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="growth_engine.funnel_sheet", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--months", required=True, help="месяцы через запятую: 2026-05,2026-08")
    parser.add_argument("--scope", help="кабинет; по умолчанию первый из системы воронки")
    parser.add_argument("--start-row", type=int, default=26, help="строка начала блока; по умолчанию 26")
    add_store_arguments(parser)
    args = parser.parse_args(argv)
    if not getattr(args, "google_book", None):
        print("❌ блок пишется только в книгу Google: нужен --google-book")
        return 1
    try:
        return run(args)
    except GuardViolation as exc:
        print(f"❌ блок воронки не записан: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
