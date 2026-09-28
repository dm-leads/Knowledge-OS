"""Шесть операций контракта хранилища (задача 5.3б) на бэкенде-таблице: ревизии, чтение обратно, стражи при записи и
при чтении. Тесты параметризованы бэкендом: сейчас — таблица в памяти; 5.4 добавит файлы markdown, 5.5 — Lark."""
from dataclasses import replace
from datetime import date

import pytest

from growth_engine.core.artifacts import Branch, DecisionEntry, GoalTree, KnowledgeEntry, SourceMapEntry
from growth_engine.core.cycle import measure
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.core.registry import CHANGE_ABSOLUTE, HStatus
from growth_engine.core.storage import COLUMNS, SHEETS, SYSTEM_COLUMNS, RegistryStore, number_id
from growth_engine.storage.lark_sheets import LarkBackend
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.storage.memory import MemoryBackend
from growth_engine.storage.google_sheets import GoogleBridge
from growth_engine.storage.labels import LabelledBackend
from growth_engine.tests.fake_google import FakeSpreadsheet
from growth_engine.tests.fake_lark import FakeLarkBridge
from growth_engine.tests.helpers import LAUNCH, NOISE_SHARE, candidate, n
from growth_engine.tests.test_artifacts import CLASS_STATUS, route
from growth_engine.tests.test_measure_and_state import launched, share

DAY = date(2026, 9, 15)
BACKENDS = {"память": lambda tmp_path: MemoryBackend(), "markdown": lambda tmp_path: MarkdownBackend(tmp_path / "реестр"),
            "lark (модель моста)": lambda tmp_path: LarkBackend("книга-проба", FakeLarkBridge()),
            "google (модель API)": lambda tmp_path: LarkBackend("книга-проба", GoogleBridge(
                open_book=lambda token, book=FakeSpreadsheet(): book, sleep=lambda seconds: None)),
            "google с подписями": lambda tmp_path: LabelledBackend(LarkBackend("книга-проба", GoogleBridge(
                open_book=lambda token, book=FakeSpreadsheet(): book, sleep=lambda seconds: None)))}
KNOWLEDGE = KnowledgeEntry(id="K-001", statement="шаг 2 формы подбора не барьер", verdict="опровергнуто",
                           on=date(2026, 10, 20), source="C:analytics-x:data", hypothesis_id="H-001")
DECISION = DecisionEntry("Ц-0", "H1 → добыча данных", "починка атрибуции", subtraction="гипотеза H1 снята",
                         id="D-v1-H1")
SOURCE = SourceMapEntry(name="аналитика-x", source_class="C", status="подключён", history_from="2021-04",
                        truth_point="заявки", probe="analytics/data за 1 день", secret_env_names=("API_KEY_X",))
# Дерево цели строит модель: у каждого её числа часть «запрос» источника называет расчёт. Иначе цель окна (1 315) и
# эффект гипотезы в единицах цели (12) — одна метрика, период и источник — получили бы один номер (стоп стражем 13).
TREE = GoalTree(goal=n("sales_entry", 1315, status=Status.ESTIMATE, source="C:модель:цель окна"),
                branches=(Branch("B1", "веб-поток", n("sales_entry", 400, flow="web", status=Status.ESTIMATE,
                                                      source="C:модель:потолок ветки")),
                          Branch("B2", "без визита", n("sales_entry", 300, flow="no_visit", status=Status.ESTIMATE,
                                                       source="C:модель:потолок ветки"))))


@pytest.fixture(params=list(BACKENDS))
def backend(request, tmp_path):
    return BACKENDS[request.param](tmp_path)


def store(backend):
    return RegistryStore(backend, allowed_domains=(), today=lambda: DAY)


def hypothesis_rows(backend):
    return backend.read_rows(SHEETS["hypotheses"])


def launch_h001(s):
    s.create(candidate())
    s.declare_expectation("H-001", NOISE_SHARE, 0.004, CHANGE_ABSOLUTE)
    s.update_status("H-001", HStatus.IN_TEST, **LAUNCH)


# --- создать и прочитать ---

def test_create_writes_numbers_first_and_reads_back(backend):
    reports = store(backend).create(candidate())
    assert [(r.artifact, r.rows_written, r.rows_read_back) for r in reports] == [
        ("Модель — снимки", 3, 3), ("Гипотезы", 1, 1)]
    assert store(backend).read("hypotheses") == [candidate()]
    assert backend.header(SHEETS["hypotheses"]) == list(COLUMNS["hypotheses"] + SYSTEM_COLUMNS)


def test_foreign_sheet_with_the_same_name_is_not_taken(backend):
    """Лист людей с именем листа движка (в книге Lark — «Гипотезы» карты v1.0) не занимается: колонки движка не
    дописываются справа от чужой шапки, команда останавливается и называет причину."""
    sheet = SHEETS["hypotheses"]
    backend.create_sheet(sheet, ["#", "Идея", "Кто предложил"])
    backend.write_rows(sheet, [(None, {"#": "H1", "Идея": "наследовать маркер", "Кто предложил": "Дмитрий"})])
    with pytest.raises(GuardViolation, match="не лист движка") as e:
        store(backend).create(candidate())
    assert e.value.guard == 13 and backend.header(sheet) == ["#", "Идея", "Кто предложил"]


def test_repeated_create_writes_nothing(backend):
    s = store(backend)
    s.create(candidate())
    assert [r.rows_written for r in s.create(candidate())] == [0, 0]


def test_same_id_with_other_content_stops(backend):
    s = store(backend)
    s.create(candidate())
    with pytest.raises(GuardViolation) as e:
        s.create(replace(candidate(), owner="Другой владелец"))
    assert e.value.guard == 13 and s.read("hypotheses") == [candidate()]


def test_same_number_id_with_other_value_stops(backend):
    s = store(backend)
    s.create(n("sales_entry", 587))
    with pytest.raises(GuardViolation, match="«запрос»") as e:
        s.create(n("sales_entry", 600))
    assert e.value.guard == 13


def test_pii_stops_before_backend_is_touched(backend):
    with pytest.raises(GuardViolation) as e:
        store(backend).create(replace(candidate(), change="позвонить +7 999 123-45-67"))
    assert e.value.guard == 12
    assert backend.header(SHEETS["numbers"]) is None and backend.header(SHEETS["hypotheses"]) is None


def test_read_filters_by_fields(backend):
    s = store(backend)
    s.create(candidate())
    s.create(candidate("H-002"))
    s.declare_expectation("H-002", NOISE_SHARE, 0.004, CHANGE_ABSOLUTE)
    s.update_status("H-002", HStatus.IN_TEST, **LAUNCH)
    assert [h.id for h in s.read("hypotheses", status=HStatus.IN_TEST)] == ["H-002"]


# --- обновления идут через доменные правила ---

def test_gate_launch_and_measurement_go_through_domain_rules(backend):
    s = store(backend)
    launch_h001(s)
    assert s.read("hypotheses") == [launched()]
    s.add_measurement("H-001", share(1640))
    stored, = s.read("hypotheses")
    assert stored == measure(launched(), share(1640)) and stored.in_threshold is False
    row, = hypothesis_rows(backend)
    assert (row["revision"], row["updated_at"]) == ("4", "2026-09-15")


def test_launch_without_gate_is_refused_and_row_kept(backend):
    s = store(backend)
    s.create(candidate())
    with pytest.raises(GuardViolation) as e:
        s.update_status("H-001", HStatus.IN_TEST, **LAUNCH)
    assert e.value.guard == 5
    row, = hypothesis_rows(backend)
    assert row["revision"] == "1" and s.read("hypotheses") == [candidate()]


def test_update_of_absent_hypothesis_stops(backend):
    with pytest.raises(GuardViolation) as e:
        store(backend).update_status("H-404", HStatus.RESEARCH)
    assert e.value.guard == 13


def test_link_to_tree_updates_branch_and_hypothesis(backend):
    s = store(backend)
    s.create(TREE)
    s.create(candidate())
    first = s.link_to_tree("H-001", number_id(TREE.goal), "B2")
    assert [(r.artifact, r.rows_written) for r in first] == [("Дерево цели", 1), ("Модель — снимки", 0),
                                                            ("Гипотезы", 1)]
    tree, = s.read("trees")
    assert tree.branches[1].hypothesis_ids == ("H-001",) and s.read("hypotheses")[0].tree_branch == "B2"
    assert [r.rows_written for r in s.link_to_tree("H-001", number_id(TREE.goal), "B2")] == [0, 0]


def test_route_needs_class_status_and_valid_steps(backend):
    s = store(backend)
    with pytest.raises(GuardViolation) as e:
        s.create(route())
    assert e.value.guard == 9
    with pytest.raises(GuardViolation):
        s.create(replace(route(), steps=route().steps[:-1]), class_status=CLASS_STATUS)
    assert backend.header(SHEETS["routes"]) is None
    s.create(route(), class_status=CLASS_STATUS)
    assert s.read("routes", class_status=CLASS_STATUS) == [route()]


def test_export_snapshot_lists_all_seven_and_missing_sheets(backend):
    s = store(backend)
    for record in (candidate(), KNOWLEDGE, DECISION, SOURCE):
        s.create(record)
    snapshot = s.export_snapshot()
    assert set(snapshot.artifacts) == set(SHEETS)
    assert (snapshot.artifacts["hypotheses"], snapshot.artifacts["knowledge"], snapshot.artifacts["decisions"],
            snapshot.artifacts["sources"]) == ([candidate()], [KNOWLEDGE], [DECISION], [SOURCE])
    assert set(snapshot.missing) == {"Дерево цели", "Маршрут цикла"}


# --- ручные правки и шапка листа ---

def test_manual_edit_that_breaks_rules_stops_reading(backend):
    s = store(backend)
    launch_h001(s)
    backend.write_rows(SHEETS["hypotheses"], [(0, {"zone": "чужая"})])
    with pytest.raises(GuardViolation) as e:
        s.read("hypotheses")
    assert e.value.guard == 7


def test_human_columns_survive_updates(backend):
    s = store(backend)
    s.create(candidate())
    backend.add_columns(SHEETS["hypotheses"], ["комментарий"])
    backend.write_rows(SHEETS["hypotheses"], [(0, {"комментарий": "обсудить с владельцем"})])
    s.declare_expectation("H-001", NOISE_SHARE, 0.004, CHANGE_ABSOLUTE)
    row, = hypothesis_rows(backend)
    assert (row["комментарий"], row["revision"]) == ("обсудить с владельцем", "2")


def test_missing_engine_column_is_added_at_the_end(backend):
    sheet = SHEETS["knowledge"]
    backend.create_sheet(sheet, ["id", "statement", "комментарий"])
    store(backend).create(KNOWLEDGE)
    header = backend.header(sheet)
    assert header[:3] == ["id", "statement", "комментарий"]
    assert set(COLUMNS["knowledge"] + SYSTEM_COLUMNS) <= set(header)
    assert store(backend).read("knowledge") == [KNOWLEDGE]


def test_duplicated_engine_column_stops(backend):
    backend.create_sheet(SHEETS["knowledge"], ["id", "statement", "statement"])
    with pytest.raises(GuardViolation, match="повторяются") as e:
        store(backend).create(KNOWLEDGE)
    assert e.value.guard == 13


def test_duplicated_row_id_stops(backend):
    s = store(backend)
    s.create(KNOWLEDGE)
    row, = backend.read_rows(SHEETS["knowledge"])
    backend.write_rows(SHEETS["knowledge"], [(None, row)])
    with pytest.raises(GuardViolation, match="повторяется") as e:
        s.create(replace(KNOWLEDGE, id="K-002"))
    assert e.value.guard == 13


# --- порция записей и заведение листов ---

def test_batch_writes_one_report_per_sheet_and_repeat_writes_nothing(backend):
    s = store(backend)
    records = [candidate(), candidate("H-002"), KNOWLEDGE, DECISION,
               replace(DECISION, id="D-v1-H6", decided="H6 → добыча данных")]
    reports = s.create_batch(records)
    assert [(r.artifact, r.rows_written, r.rows_read_back) for r in reports] == [
        ("Модель — снимки", 3, 3), ("Гипотезы", 2, 2), ("Карта знаний", 1, 1), ("Журнал решений", 2, 2)]
    assert [r.rows_written for r in s.create_batch(records)] == [0, 0, 0, 0]
    assert s.read("hypotheses") == [candidate(), candidate("H-002")] and len(s.read("decisions")) == 2


def test_batch_stops_before_writing_on_repeated_id_pii_or_tree(backend):
    s = store(backend)
    with pytest.raises(GuardViolation, match="дважды") as e:
        s.create_batch([DECISION, DECISION])
    assert e.value.guard == 13
    with pytest.raises(GuardViolation) as e:
        s.create_batch([DECISION, replace(candidate(), change="позвонить +7 999 123-45-67")])
    assert e.value.guard == 12
    with pytest.raises(GuardViolation, match="create") as e:
        s.create_batch([TREE])
    assert e.value.guard == 13
    assert all(backend.header(sheet) is None for sheet in SHEETS.values())


def test_ensure_sheets_creates_seven_empty_sheets_once(backend):
    s = store(backend)
    assert s.ensure_sheets() == list(SHEETS.values())
    assert s.ensure_sheets() == [] and s.export_snapshot().missing == ()
    assert all(backend.read_rows(sheet) == [] for sheet in SHEETS.values())


# --- выгрузка карты источников ---

def test_sync_sources_appends_updates_and_keeps_absent_rows(backend):
    s = store(backend)
    other = replace(SOURCE, name="аналитика-w", secret_env_names=())
    report, absent = s.sync_sources([SOURCE, other])
    assert (report.rows_written, report.rows_read_back, absent) == (2, 2, ())
    report, absent = s.sync_sources([SOURCE, other])
    assert (report.rows_written, absent) == (0, ())
    changed = replace(SOURCE, truth_point="заявки по каналам и страницам")
    report, absent = s.sync_sources([changed])
    assert (report.rows_written, report.rows_read_back, absent) == (1, 1, ("аналитика-w",))
    assert s.read("sources") == [changed, other]
    assert [row["revision"] for row in backend.read_rows(SHEETS["sources"])] == ["2", "1"]


def test_sync_sources_with_repeated_name_stops(backend):
    with pytest.raises(GuardViolation, match="повторяются") as e:
        store(backend).sync_sources([SOURCE, SOURCE])
    assert e.value.guard == 13 and backend.header(SHEETS["sources"]) is None


def test_repeated_row_id_stops_reading_too(backend):
    s = store(backend)
    s.create(n("sales_entry", 587))
    row, = backend.read_rows(SHEETS["numbers"])
    backend.write_rows(SHEETS["numbers"], [(None, row)])
    with pytest.raises(GuardViolation, match="повторяется") as e:
        s.read("numbers")
    assert e.value.guard == 13


def test_read_without_verification_only_decodes(backend):
    s = store(backend)
    launch_h001(s)
    backend.write_rows(SHEETS["hypotheses"], [(0, {"zone": "чужая"})])
    hypothesis, = s.read("hypotheses", verify=False)
    assert hypothesis.zone == "чужая"
    with pytest.raises(GuardViolation) as e:
        s.read("hypotheses")
    assert e.value.guard == 7


# --- гонки и потерянная запись (только таблица в памяти) ---

class RacyBackend(MemoryBackend):
    """Другой процесс меняет ревизию строки гипотезы между чтением гипотезы и записью."""

    def __init__(self):
        super().__init__()
        self.reads, self.race_on = 0, None

    def read_rows(self, sheet):
        if sheet == SHEETS["hypotheses"]:
            self.reads += 1
            if self.reads == self.race_on:
                self.sheets[sheet]["rows"][0]["revision"] = "7"
        return super().read_rows(sheet)


class LossyBackend(MemoryBackend):
    """Запись теряет последнюю строку порции."""

    def write_rows(self, sheet, items):
        super().write_rows(sheet, items[:-1])


def test_row_changed_by_another_process_stops_update():
    racy = RacyBackend()
    s = store(racy)
    s.create(candidate())
    racy.race_on = racy.reads + 2
    with pytest.raises(GuardViolation, match="другим процессом") as e:
        s.declare_expectation("H-001", NOISE_SHARE, 0.004, CHANGE_ABSOLUTE)
    assert e.value.guard == 13


def test_lost_write_is_caught_by_read_back():
    with pytest.raises(GuardViolation) as e:
        store(LossyBackend()).create(candidate())
    assert e.value.guard == 13
