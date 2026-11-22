"""Сборка рабочей книги Google (24.09.2026): три листа для человека, служебное движка скрыто.

Решения владельца: рабочие листы — «Гипотезы», «Воронка и оценка гипотез», «Юнит-экономика»; служебные листы
движка — в той же книге, скрыты; у «Гипотез» рабочие колонки слева, служебные скрыты. Планировщик — чистая функция
от состояния книги, поэтому повторный запуск на собранной книге ничего не делает.
"""
from growth_engine.core.storage import COLUMNS, SHEETS, SYSTEM_COLUMNS
from growth_engine.google_book import (CARD_WIDTHS, COPIED, HYPOTHESIS_NOTES, HYPOTHESIS_VISIBLE,
                                       HYPOTHESIS_VISIBLE_CARD, WORKING_ORDER, hidden_range, hypothesis_header, plan,
                                       ref_errors)

FRONT = len(HYPOTHESIS_VISIBLE) + len(HYPOTHESIS_VISIBLE_CARD)     # видимые колонки слева в книге 1.9.1
from growth_engine.storage.labels import LABELS


def empty_book():
    return {"folder": None, "book": None, "sheets": {}}


def built_book():
    """Состояние книги после полной сборки — по тому, что планировщик сам бы сделал."""
    sheets = {}
    for index, title in enumerate(WORKING_ORDER + [SHEETS[k] for k in SHEETS if k != "hypotheses"]):
        header = (hypothesis_header() if title == SHEETS["hypotheses"] else
                  list(COLUMNS[next(k for k, v in SHEETS.items() if v == title)]) + list(SYSTEM_COLUMNS)
                  if title in SHEETS.values() else ["что-то"])
        sheets[title] = {"index": index, "hidden": title not in WORKING_ORDER, "header": header, "empty": False,
                         "hidden_columns": (FRONT, len(header)) if title == SHEETS["hypotheses"]
                         else None, "frozen_rows": 1 if title == SHEETS["hypotheses"] else 0,
                         "labelled": True, "styled": title == SHEETS["hypotheses"],
                         "notes": title == SHEETS["hypotheses"],
                         "widths": dict(CARD_WIDTHS) if title == SHEETS["hypotheses"] else {}}
    return {"folder": "папка", "book": "книга", "sheets": sheets}


def kinds(actions):
    return [action[0] for action in actions]


def test_hypothesis_header_puts_working_columns_first_and_keeps_every_engine_column_once():
    header = hypothesis_header()
    engine = list(COLUMNS["hypotheses"]) + list(SYSTEM_COLUMNS)
    assert header[:FRONT] == list(HYPOTHESIS_VISIBLE) + list(HYPOTHESIS_VISIBLE_CARD)
    assert sorted(header) == sorted(engine) and len(header) == len(set(header))


def test_working_columns_hide_no_number_references():
    """Номер числа (`*_id`) человеку ничего не говорит — он остаётся в скрытой части."""
    assert not [column for column in HYPOTHESIS_VISIBLE if column.endswith("_id") and column != "id"]


def test_empty_drive_gets_folder_book_copies_engine_sheets_and_hiding():
    actions = plan(empty_book())
    assert kinds(actions)[:2] == ["create_folder", "create_book"]
    assert [a[1] for a in actions if a[0] == "copy"] == list(COPIED)
    created = [a[1] for a in actions if a[0] == "create_sheet"]
    assert sorted(created) == sorted(SHEETS.values())
    hidden = [a[1] for a in actions if a[0] == "hide_sheet"]
    assert sorted(hidden) == sorted(v for k, v in SHEETS.items() if k != "hypotheses")
    assert ("hide_columns", SHEETS["hypotheses"], FRONT, len(hypothesis_header())) in actions
    assert ("order", WORKING_ORDER) in actions


def test_copies_go_before_the_sheets_that_refer_to_them():
    """«Воронка» ссылается формулами на «Юнит-экономику»: копия, у которой нет листа-адресата, даёт #REF!."""
    assert list(COPIED).index("Юнит-экономика") < list(COPIED).index("Воронка и оценка гипотез")


def test_built_book_needs_nothing():
    assert plan(built_book()) == []


def test_default_empty_sheet_of_a_new_book_is_deleted_but_a_filled_one_is_not():
    state = built_book()
    state["sheets"]["Лист1"] = {"index": 20, "hidden": False, "header": [], "empty": True, "hidden_columns": None,
                                "frozen_rows": 0}
    state["sheets"]["Заметки"] = {"index": 21, "hidden": False, "header": ["x"], "empty": False,
                                  "hidden_columns": None, "frozen_rows": 0}
    assert plan(state) == [("delete_sheet", "Лист1")]


def test_people_sheet_under_the_hypotheses_name_is_not_rebuilt():
    """Лист людей «Гипотезы» без колонки id — не лист движка: сборка останавливается, а не пишет поверх."""
    state = built_book()
    state["sheets"]["Гипотезы"]["header"] = ["#", "Гипотеза"]
    actions = plan(state)
    assert actions == [("stop", "«Гипотезы»: лист есть, но это не лист движка (нет колонки id)")]


def test_ref_errors_counts_broken_references():
    assert ref_errors([["=", "#REF!"], [1, "#REF!", "ок"], []]) == 2


def test_latin_header_is_relabelled_without_touching_people_columns():
    """Книга, собранная до подписей: шапка переписывается подписями, колонка людей остаётся как есть."""
    state = built_book()
    sheet = state["sheets"]["Карта знаний"]
    sheet["labelled"] = False
    sheet["raw_header"] = ["id", "statement", "моя заметка"] + sheet["header"][2:]
    actions = plan(state)
    relabel = [a for a in actions if a[0] == "relabel"]
    assert relabel == [("relabel", "Карта знаний",
                        ["№", "Утверждение", "моя заметка"] + [LABELS.get(c, c) for c in sheet["header"][2:]])]


def test_hypotheses_sheet_is_styled_and_noted_once():
    state = built_book()
    state["sheets"]["Гипотезы"].update(styled=False, notes=False)
    assert [a[0] for a in plan(state)] == ["style", "notes"]
    assert plan(built_book()) == []


def test_every_working_column_has_a_note():
    assert sorted(HYPOTHESIS_NOTES) == sorted(HYPOTHESIS_VISIBLE + HYPOTHESIS_VISIBLE_CARD)
    assert all(len(note) > 10 for note in HYPOTHESIS_NOTES.values())


def test_people_columns_get_their_width_once():
    """«ТЗ исполнителю» в 2 000 знаков в колонке по умолчанию — узкий высокий столбец; ширина ставится один раз."""
    state = built_book()
    sheet = state["sheets"]["Гипотезы"]
    sheet["header"] = sheet["header"] + ["Исполнитель", "ТЗ исполнителю"]
    sheet["widths"] = {**CARD_WIDTHS, "Исполнитель": 100, "ТЗ исполнителю": 100}
    assert plan(state) == [("widen", "Гипотезы", {"Исполнитель": 180, "ТЗ исполнителю": 480})]
    sheet["widths"] = {**CARD_WIDTHS, "Исполнитель": 180, "ТЗ исполнителю": 480}
    assert plan(state) == []


def test_people_columns_hidden_by_mistake_are_shown_and_engine_ones_stay_hidden():
    """Живая сборка 24.09.2026 спрятала колонки людей: прятала «от 16-й до конца шапки», а они стоят в конце."""
    state = built_book()
    sheet = state["sheets"]["Гипотезы"]
    engine_end = len(sheet["header"])
    sheet["header"] = sheet["header"] + ["Исполнитель", "ТЗ исполнителю"]
    sheet["widths"] = {**CARD_WIDTHS, "Исполнитель": 180, "ТЗ исполнителю": 480}
    sheet["hidden_columns"] = (FRONT, engine_end + 2)
    assert plan(state) == [("show_columns", "Гипотезы", engine_end, engine_end + 2)]


def old_book_with_card_columns():
    """Книга, собранная до 1.9.0: рабочие колонки слева, служебные скрыты, колонки людей в конце; хранилище 1.9.0
    дописало колонки карточки запуска за колонками людей."""
    state = built_book()
    sheet = state["sheets"]["Гипотезы"]
    old = list(HYPOTHESIS_VISIBLE) + [c for c in hypothesis_header()
                                     if c not in HYPOTHESIS_VISIBLE and c not in HYPOTHESIS_VISIBLE_CARD]
    sheet["header"] = old + ["Исполнитель", "ТЗ исполнителю"] + list(HYPOTHESIS_VISIBLE_CARD)
    sheet["hidden_columns"] = (len(HYPOTHESIS_VISIBLE), len(old))
    sheet["widths"] = {**CARD_WIDTHS, "Исполнитель": 180, "ТЗ исполнителю": 480}
    return state, len(old)


def test_card_columns_appended_to_an_old_book_stay_visible_and_people_columns_are_not_hidden():
    """Ревью 22.11.2026: прятать «от 16-й до последней колонки движка» после 1.9.0 спрятало бы колонки людей, за
    которыми хранилище дописало колонки карточки. Прячется только сплошной кусок служебных колонок."""
    state, old_end = old_book_with_card_columns()
    assert hidden_range(state["sheets"]["Гипотезы"]["header"]) == (len(HYPOTHESIS_VISIBLE), old_end)
    assert plan(state) == []


def test_columns_hidden_too_far_in_an_old_book_are_shown_again():
    state, old_end = old_book_with_card_columns()
    sheet = state["sheets"]["Гипотезы"]
    sheet["hidden_columns"] = (len(HYPOTHESIS_VISIBLE), len(sheet["header"]))
    assert plan(state) == [("show_columns", "Гипотезы", old_end, len(sheet["header"]))]


def test_card_columns_of_an_old_book_get_notes_and_width_where_they_stand():
    state, _ = old_book_with_card_columns()
    sheet = state["sheets"]["Гипотезы"]
    sheet.update(notes=False, widths={"Исполнитель": 180, "ТЗ исполнителю": 480})
    assert plan(state) == [("notes", "Гипотезы"), ("widen", "Гипотезы", dict(CARD_WIDTHS))]


def test_engine_column_opened_inside_the_hidden_block_is_hidden_again():
    """Ревью Codex 22.11.2026: человек открыл одну служебную колонку внутри скрытого куска — первая и последняя
    скрытые прежние, но сборка должна спрятать кусок заново."""
    state = built_book()
    state["sheets"]["Гипотезы"]["hidden_gaps"] = True
    assert plan(state) == [("hide_columns", "Гипотезы", FRONT, len(hypothesis_header()))]


def test_workbench_retries_the_per_minute_limit(tmp_path, monkeypatch):
    """Живая сборка 24.09.2026 упала на проверке #REF!: Google ответил 429 на минутный лимит чтения, а сборщик,
    в отличие от моста, не ждал и не повторял. Клиент gspread с паузой и повтором закрывает это для всех вызовов."""
    import json

    import gspread
    from gspread.http_client import BackOffHTTPClient

    from growth_engine import google_book

    token = tmp_path / "token.json"
    token.write_text(json.dumps({"client_id": "id", "client_secret": "secret", "refresh_token": "refresh",
                                 "token": "access"}), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_SHEETS_TOKEN_PATH", str(token))
    seen = {}
    monkeypatch.setattr(gspread, "authorize", lambda creds, http_client=None: seen.setdefault("client", http_client))
    google_book.Workbench.with_user_token()
    assert seen["client"] is BackOffHTTPClient
