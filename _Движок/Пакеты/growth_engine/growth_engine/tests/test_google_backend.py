"""Google-бэкенд хранилища (24.09.2026): мост с командами Lark-моста поверх Google Sheets API.

Хранилище перенесено в Google, когда у Lark кончилась месячная квота вызовов. Логика таблицы общая с Lark
(`LarkBackend`), поэтому здесь проверяется только то, чем Google отличается: лист в диапазоне задаётся именем, пустой
диапазон приходит без значений, запись идёт в режиме RAW, отказ ключа — нехватка доступа, минутный лимит — повтор.
"""
from argparse import ArgumentParser, Namespace

import pytest
from google.auth.exceptions import RefreshError

from growth_engine.core.errors import GuardViolation
from growth_engine.core.storage import RegistryStore
from growth_engine.storage.google_sheets import GoogleBridge
from growth_engine.storage.lark_sheets import LarkBackend
from growth_engine.storage.selection import add_store_arguments, open_store
from growth_engine.tests.fake_google import FakeSpreadsheet, api_error
from growth_engine.tests.helpers import candidate


def bridge_for(book: FakeSpreadsheet, sleep=lambda seconds: None) -> GoogleBridge:
    return GoogleBridge(open_book=lambda token: book, sleep=sleep)


def test_tabs_report_ids_titles_and_sizes():
    book = FakeSpreadsheet()
    gid = book.add("Модель — снимки", rows=10, columns=5)
    assert bridge_for(book).call("tabs", book="книга") == {
        "tabs": [{"sheet_id": str(gid), "title": "Модель — снимки", "row_count": 10, "column_count": 5,
                  "hidden": False}]}


def test_read_names_the_sheet_by_title_and_returns_empty_range_as_no_rows():
    book = FakeSpreadsheet()
    gid = book.add("Реестр гипотез", rows=5, columns=3)
    book.sheets[gid]["grid"][0][:2] = ["id", "status"]
    bridge = bridge_for(book)
    bridge.call("tabs", book="книга")
    result = bridge.call("read", book="книга", ranges=[f"{gid}!A1:C2", f"{gid}!A3:C5"])
    assert result["value_ranges"][0]["values"] == [["id", "status"]]
    assert result["value_ranges"][1]["values"] == []


def test_title_with_apostrophe_is_quoted():
    book = FakeSpreadsheet()
    gid = book.add("Карта d'знаний", rows=3, columns=2)
    bridge = bridge_for(book)
    bridge.call("tabs", book="книга")
    bridge.call("write", book="книга", value_ranges=[{"range": f"{gid}!A1:B1", "values": [["id", "x"]]}])
    assert book.sheets[gid]["grid"][0][:2] == ["id", "x"]


@pytest.mark.parametrize("text", ["=1+1", "TRUE", "false", "'кавычка"])
def test_write_keeps_text_as_text(text):
    """Режим RAW: строка с «=» не становится формулой, «TRUE» — логическим; кодек ячеек рассчитывает на это."""
    book = FakeSpreadsheet()
    gid = book.add("Лист", rows=2, columns=2)
    bridge = bridge_for(book)
    bridge.call("tabs", book="книга")
    bridge.call("write", book="книга", value_ranges=[{"range": f"{gid}!A1:A1", "values": [[text]]}])
    assert book.sheets[gid]["grid"][0][0] == text


def test_addtab_creates_a_sheet_the_size_of_a_new_lark_sheet():
    book = FakeSpreadsheet()
    bridge = bridge_for(book)
    reply = bridge.call("addtab", book="книга", title="Карта знаний")
    tab = bridge.call("tabs", book="книга")["tabs"][0]
    assert reply["sheet_id"] == tab["sheet_id"] and (tab["row_count"], tab["column_count"]) == (200, 20)


def test_second_sheet_with_the_same_title_stops():
    book = FakeSpreadsheet()
    book.add("Карта знаний")
    with pytest.raises(GuardViolation, match="already exists") as e:
        bridge_for(book).call("addtab", book="книга", title="Карта знаний")
    assert e.value.guard == 13


def test_insert_puts_rows_and_columns_before_the_position():
    book = FakeSpreadsheet()
    gid = book.add("Лист", rows=3, columns=2)
    book.sheets[gid]["grid"][2][1] = "низ"
    bridge = bridge_for(book)
    bridge.call("tabs", book="книга")
    bridge.call("insert", book="книга", sheet=str(gid), position="3", count=2)
    bridge.call("insert", book="книга", sheet=str(gid), position="B", count=1)
    grid = book.sheets[gid]["grid"]
    assert (len(grid), len(grid[0])) == (5, 3) and grid[4][2] == "низ"


def test_hide_hides_a_sheet():
    book = FakeSpreadsheet()
    gid = book.add("Маршрут цикла")
    bridge = bridge_for(book)
    bridge.call("hide", book="книга", sheet=str(gid))
    assert book.sheets[gid]["hidden"] is True


def test_unknown_sheet_number_stops():
    book = FakeSpreadsheet()
    with pytest.raises(GuardViolation, match="листа с номером 999 нет") as e:
        bridge_for(book).call("read", book="книга", ranges=["999!A1:B2"])
    assert e.value.guard == 13


# --- отказы ---

def test_refused_key_is_missing_access_not_empty():
    book = FakeSpreadsheet()
    book.fail_with = RefreshError("invalid_grant: Token has been expired or revoked.")
    with pytest.raises(GuardViolation, match="нет доступа к Google") as e:
        bridge_for(book).call("tabs", book="книга")
    assert e.value.guard == 13 and e.value.severity == GuardViolation.COVERAGE


def test_api_error_text_hides_emails_and_tokens():
    book = FakeSpreadsheet()
    book.fail_with = api_error(403, "The caller dimaz@example.com has no access, key AIzaSyA1234567890abcdefghijklmnop",
                               "PERMISSION_DENIED")
    with pytest.raises(GuardViolation) as e:
        bridge_for(book).call("tabs", book="книга")
    assert "@" not in str(e.value) and "AIzaSy" not in str(e.value) and "403" in str(e.value)


def test_rate_limit_is_retried_after_a_pause():
    """Минутный лимит Google — временный: мост ждёт и повторяет, а не роняет команду на середине записи."""
    book = FakeSpreadsheet()
    book.add("Лист")
    book.fail_with = api_error(429, "Quota exceeded for quota metric 'Read requests'", "RESOURCE_EXHAUSTED")
    pauses = []

    def sleep(seconds):
        pauses.append(seconds)
        book.fail_with = None

    tabs = bridge_for(book, sleep).call("tabs", book="книга")["tabs"]
    assert [tab["title"] for tab in tabs] == ["Лист"] and len(pauses) == 1 and pauses[0] > 0


def test_rate_limit_that_does_not_pass_stops_with_the_reason():
    book = FakeSpreadsheet()
    book.fail_with = api_error(429, "Quota exceeded", "RESOURCE_EXHAUSTED")
    pauses = []
    with pytest.raises(GuardViolation, match="лимит") as e:
        bridge_for(book, pauses.append).call("tabs", book="книга")
    assert e.value.guard == 13 and len(pauses) >= 2


# --- хранилище целиком ---

def test_registry_round_trip_through_google():
    book = FakeSpreadsheet()
    store = RegistryStore(LarkBackend("книга", bridge_for(book)), allowed_domains=())
    hypothesis = candidate()
    reports = store.create(hypothesis)
    assert [report.rows_written for report in reports] == [report.rows_read_back for report in reports]
    assert store.read("hypotheses", id=hypothesis.id) == [hypothesis]


def test_store_key_google_book_opens_a_google_bridge():
    parser = ArgumentParser()
    add_store_arguments(parser)
    args = parser.parse_args(["--google-book", "книга-проба"])
    book = FakeSpreadsheet()
    with open_store(args, (), google_factory=lambda: bridge_for(book)) as (store, label):
        store.create(candidate())
    assert label == "Google, книга книга-проба" and any(s["title"] == "Гипотезы" for s in book.sheets.values())


def test_given_store_keys_names_every_store_key():
    from growth_engine.storage.selection import given_store_keys
    assert given_store_keys(Namespace(out=None, lark_book=None, google_book="книга")) == ["--google-book"]
    assert given_store_keys(Namespace(out="папка", lark_book="книга")) == ["--out", "--lark-book"]
    assert given_store_keys(Namespace()) == []


def test_lark_bridge_script_follows_the_environment(tmp_path, monkeypatch):
    """Путь моста Lark — из окружения: в пакете движка скрипт моста лежит не рядом с кодом инстанса."""
    from growth_engine.storage.lark_sheets import bridge_script
    monkeypatch.setenv("LARK_SHEETS_BRIDGE", str(tmp_path / "мост.mjs"))
    assert bridge_script() == tmp_path / "мост.mjs"
    monkeypatch.delenv("LARK_SHEETS_BRIDGE")
    assert bridge_script().name == "lark_sheets_cli.mjs"


def test_table_backend_is_the_generic_name_of_the_table_logic():
    from growth_engine.storage.lark_sheets import LarkBackend
    from growth_engine.storage.table import TableBackend
    assert LarkBackend is TableBackend


def test_google_book_and_folder_together_stop():
    with pytest.raises(GuardViolation) as e:
        with open_store(Namespace(out="папка", lark_book=None, google_book="книга"), ()):
            pass
    assert e.value.guard == 13
