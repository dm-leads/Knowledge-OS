"""Markdown-бэкенд контракта хранилища (задача 5.4): ячейки без потерь, читаемая таблица, те же операции, что в памяти;
порция чисел для команд."""
import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.storage import RegistryStore
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.storage.memory import MemoryBackend
from growth_engine.tests.helpers import n
from growth_engine.tests.test_storage_contract import DAY, DECISION, KNOWLEDGE, SOURCE, TREE, launch_h001

TRICKY = ["a | b", "первая\nвторая", "&amp; и &#124; буквально", "  пробелы по краям  ", "", "'=формула", "\r\n", "|"]


def test_cells_survive_file_round_trip(tmp_path):
    backend = MarkdownBackend(tmp_path)
    backend.create_sheet("лист", ["id", "текст"])
    backend.write_rows("лист", [(None, {"id": str(i), "текст": value}) for i, value in enumerate(TRICKY)])
    assert [row["текст"] for row in MarkdownBackend(tmp_path).read_rows("лист")] == TRICKY


def test_missing_sheet_is_none_and_second_create_stops(tmp_path):
    backend = MarkdownBackend(tmp_path)
    assert backend.header("нет такого листа") is None
    backend.create_sheet("лист", ["id"])
    with pytest.raises(GuardViolation) as e:
        backend.create_sheet("лист", ["id"])
    assert e.value.guard == 13


def test_file_is_a_readable_table(tmp_path):
    backend = MarkdownBackend(tmp_path)
    backend.create_sheet("Карта знаний", ["id", "statement"])
    backend.write_rows("Карта знаний", [(None, {"id": "K-001", "statement": "шаг 2 не барьер"})])
    text = (tmp_path / "Карта знаний.md").read_text(encoding="utf-8")
    assert text.splitlines()[:5] == ["# Карта знаний", "", "| id | statement |", "|---|---|", "| K-001 | шаг 2 не барьер |"]


def test_row_width_broken_by_hand_stops(tmp_path):
    backend = MarkdownBackend(tmp_path)
    backend.create_sheet("лист", ["id", "текст"])
    (tmp_path / "лист.md").write_text("# лист\n\n| id | текст |\n|---|---|\n| 1 |\n", encoding="utf-8")
    with pytest.raises(GuardViolation) as e:
        backend.read_rows("лист")
    assert e.value.guard == 13


def test_same_snapshot_in_memory_and_in_files(tmp_path):
    snapshots = []
    for backend in (MemoryBackend(), MarkdownBackend(tmp_path)):
        store = RegistryStore(backend, today=lambda: DAY)
        launch_h001(store)
        for record in (TREE, KNOWLEDGE, DECISION, SOURCE):
            store.create(record)
        snapshots.append(store.export_snapshot())
    assert snapshots[0] == snapshots[1] and snapshots[0].missing == ("Маршрут цикла",)


def test_numbers_batch_merges_repeats_and_stops_on_conflict(tmp_path):
    store = RegistryStore(MarkdownBackend(tmp_path), today=lambda: DAY)
    report = store.create_numbers([n("sales_entry", 587), n("sales_entry", 587), n("leads", 1981)])
    assert (report.rows_written, report.rows_read_back) == (2, 2)
    with pytest.raises(GuardViolation, match="«запрос»") as e:
        store.create_numbers([n("visits", 100), n("visits", 101)])
    assert e.value.guard == 13 and len(store.read("numbers")) == 2
