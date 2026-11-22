"""1.9.0, страж 7: карточка запуска по стандарту гипотез (раздел «Карточка гипотезы», 17.11.2026).

До старта объявлены, кроме порога главной метрики и критерия провала, защитная метрика с порогом ухудшения и критерии
остановки до конца окна; без исходного замера («требует замера до запуска») тест не запускается. Запуски до 1.9.0 этих
полей не имеют: читаются без ошибок и задним числом не проверяются — как порог шума в 1.8.0.
"""
import json
import math
from dataclasses import replace

import pytest

from growth_engine.core.cycle import measure
from growth_engine.core.errors import GuardViolation
from growth_engine.core.registry import (CARD_1_9_FIELDS, ZONE_PACKAGE, Decision, HStatus, check_state,
                                         is_legacy_card, transition)
from growth_engine.core.storage import (COLUMNS, HYPOTHESIS_COLUMNS_1_9, SHEETS, SYSTEM_COLUMNS, RegistryStore,
                                        hypothesis_row, number_id)
from growth_engine.storage.memory import MemoryBackend
from growth_engine.tests.helpers import EFFECT, EFFECT_RUB, FACT, LAUNCH, NOISE_SHARE, candidate, gated
from growth_engine.tests.test_measure_and_state import launched, share
from growth_engine.tests.test_registry_measure import call, card_json, launch_json
from growth_engine.tests.test_registry_measure import store as md_store
from growth_engine.tests.test_registry_measure import gated as gated_in_folder
from growth_engine.tests.test_storage_contract import BACKENDS, DAY, launch_h001


def refused(h, to=HStatus.IN_TEST, **changes):
    with pytest.raises(GuardViolation) as e:
        transition(h, to, **{**LAUNCH, **changes})
    assert e.value.guard == 7
    return str(e.value)


# --- стражи при запуске ---

def test_full_card_launches():
    h = launched()
    assert h.status is HStatus.IN_TEST and h.stop_criteria == LAUNCH["stop_criteria"]
    assert not is_legacy_card(h)


@pytest.mark.parametrize("criteria", [(), ("",), ("  ",), ("падение заявок", "")], ids=["нет", "пустой", "пробелы",
                                                                                     "один из двух пустой"])
def test_launch_without_stop_criteria_is_refused(criteria):
    assert "критерии остановки" in refused(gated(), stop_criteria=criteria)


def test_stop_criterion_that_repeats_failure_criterion_is_refused():
    reason = refused(gated(), stop_criteria=(" " + LAUNCH["failure_criterion"].upper() + " ",))
    assert "повторяет критерий провала" in reason


@pytest.mark.parametrize("changes", [{"guard_metric": ""}, {"guard_metric": "   "}, {"guard_threshold": None}],
                         ids=["нет метрики", "метрика из пробелов", "нет порога"])
def test_launch_without_guard_metric_or_threshold_is_refused(changes):
    assert "защитная метрика" in refused(gated(), **changes)


@pytest.mark.parametrize("value", [0, -0.1, 1.5, math.nan, math.inf, True],
                         ids=["ноль", "минус", "больше 1", "nan", "inf", "логическое"])
def test_guard_threshold_must_be_a_positive_share(value):
    assert "порог защитной метрики" in refused(gated(), guard_threshold=value)


def test_guard_metric_must_differ_from_main_metric():
    assert "совпадает с главной" in refused(gated(), guard_metric="cr1")


def test_missing_baseline_forbids_launch_with_its_own_reason():
    assert "требует замера до запуска" in refused(gated(), baseline_missing=True)


def test_missing_baseline_is_named_before_empty_fields():
    """Нет замера — поэтому пусты и другие поля: причина называется первой, а не «не заполнено threshold»."""
    with pytest.raises(GuardViolation) as e:
        transition(gated(baseline_missing=True), HStatus.IN_TEST)
    assert e.value.guard == 7 and "требует замера до запуска" in str(e.value)


def test_flag_from_the_card_holds_until_lifted_at_launch():
    """Флаг ставится ещё у кандидата и держит запуск, пока его не снимут вместе с замером."""
    h = gated(baseline_missing=True)
    assert h.baseline_missing is True
    refused(h)
    assert transition(h, HStatus.IN_TEST, **LAUNCH, baseline_missing=False).status is HStatus.IN_TEST


def test_waiting_owner_launch_has_the_same_guards():
    reason = refused(gated(), HStatus.WAITING_OWNER, zone=ZONE_PACKAGE, stop_criteria=())
    assert "критерии остановки" in reason


# --- записи до 1.9.0 ---

def legacy(h):
    """Запись, запущенная до 1.9.0: полей карточки 1.9.0 у неё нет."""
    return replace(h, guard_metric="", guard_threshold=None, stop_criteria=(), baseline_missing=False)


LEGACY = {
    "в тесте": lambda: legacy(launched()),
    "ждёт владельца": lambda: legacy(transition(gated(), HStatus.WAITING_OWNER, **{**LAUNCH, "zone": ZONE_PACKAGE})),
    "отложенный замер": lambda: transition(legacy(launched()), HStatus.DEFERRED,
                                           deferred_until=LAUNCH["start_date"].replace(month=11, day=15)),
    "замер": lambda: measure(legacy(launched()), share(1640)),
    "вывод": lambda: transition(measure(legacy(launched()), share(1640)), HStatus.CONCLUDED,
                                decision=Decision.KILL, conclusion="эффект ниже порога", knowledge_row="K-001"),
}


@pytest.mark.parametrize("name", list(LEGACY))
def test_launch_before_1_9_is_not_checked_retroactively(name):
    h = LEGACY[name]()
    assert is_legacy_card(h)
    check_state(h)


def test_new_rules_apply_to_launch_after_1_9_only():
    """Кандидат прежней версии без новых полей — не «старый запуск»: его запуск идёт по правилам 1.9.0."""
    assert not is_legacy_card(gated())
    assert "защитная метрика" in refused(gated(), guard_metric="", guard_threshold=None, stop_criteria=())


@pytest.mark.parametrize("erased", CARD_1_9_FIELDS)
def test_partly_erased_card_of_a_1_9_launch_fails_state_check(erased):
    """Запуск 1.9.0 пишет три поля вместе: стёртое одно — правка вне движка, а не «старая запись»."""
    h = replace(launched(), **{erased: {"guard_metric": "", "guard_threshold": None, "stop_criteria": ()}[erased]})
    assert not is_legacy_card(h)
    with pytest.raises(GuardViolation) as e:
        check_state(h)
    assert e.value.guard == 7


@pytest.mark.parametrize("cleared", [
    {"guard_metric": "", "guard_threshold": None, "stop_criteria": ()},
    {"stop_criteria": ()},
    {"baseline_missing": True},
])
def test_launch_card_cannot_be_rewritten_by_a_later_transition(cleared):
    # Ревью Codex 22.11.2026: перевод в «отложенный замер» стирал поля карточки, и запуск 1.9.0 выглядел старым.
    h = launched()
    with pytest.raises(GuardViolation) as e:
        transition(h, HStatus.DEFERRED, deferred_until=DAY, **cleared)
    assert e.value.guard == 7 and "не переписываются" in str(e.value)


def test_later_transition_keeps_launch_card_when_values_are_unchanged():
    h = launched()
    moved = transition(h, HStatus.DEFERRED, deferred_until=DAY, stop_criteria=list(h.stop_criteria))
    assert moved.stop_criteria == h.stop_criteria and not is_legacy_card(moved)


def test_store_cannot_erase_launch_card_on_deferral():
    s = RegistryStore(MemoryBackend(), allowed_domains=())
    launch_h001(s)
    with pytest.raises(GuardViolation):
        s.update_status("H-001", HStatus.DEFERRED, deferred_until=DAY,
                        guard_metric="", guard_threshold=None, stop_criteria=())
    h, = s.read("hypotheses")
    assert not is_legacy_card(h) and h.guard_metric


def test_launched_record_with_missing_baseline_fails_state_check():
    with pytest.raises(GuardViolation) as e:
        check_state(replace(legacy(launched()), baseline_missing=True))
    assert e.value.guard == 7 and "требует замера до запуска" in str(e.value)


# --- хранилище: круговой проход и реестр прежней версии во всех бэкендах ---

@pytest.fixture(params=list(BACKENDS))
def backend(request, tmp_path):
    return BACKENDS[request.param](tmp_path)


def store(backend):
    return RegistryStore(backend, allowed_domains=(), today=lambda: DAY)


def test_launch_card_round_trips_through_storage(backend):
    s = store(backend)
    launch_h001(s)
    stored, = s.read("hypotheses")
    assert stored == launched()
    assert (stored.guard_metric, stored.guard_threshold, stored.stop_criteria, stored.baseline_missing) == (
        LAUNCH["guard_metric"], LAUNCH["guard_threshold"], LAUNCH["stop_criteria"], False)
    row, = backend.read_rows(SHEETS["hypotheses"])
    assert json.loads(row["stop_criteria"]) == list(LAUNCH["stop_criteria"]) and row["baseline_missing"] == ""


def test_missing_baseline_flag_round_trips_and_holds_launch(backend):
    s = store(backend)
    s.create(candidate(baseline_missing=True))
    stored, = s.read("hypotheses")
    assert stored.baseline_missing is True and backend.read_rows(SHEETS["hypotheses"])[0]["baseline_missing"] == "да"
    s.declare_expectation("H-001", gated().noise_share, 0.0045, "абсолютное")
    with pytest.raises(GuardViolation, match="требует замера до запуска") as e:
        s.update_status("H-001", HStatus.IN_TEST, **LAUNCH)
    assert e.value.guard == 7 and s.read("hypotheses")[0].status is HStatus.CANDIDATE
    s.update_status("H-001", HStatus.IN_TEST, **LAUNCH, baseline_missing=False)
    assert s.read("hypotheses")[0].status is HStatus.IN_TEST


def old_registry(backend, records):
    """Реестр, записанный до 1.9.0: лист гипотез без колонок карточки 1.9.0, числа — на своём листе."""
    s = store(backend)
    numbers = []
    rows = []
    for record in records:
        row, used = hypothesis_row(record)
        rows.append({**{k: v for k, v in row.items() if k not in HYPOTHESIS_COLUMNS_1_9},
                     "revision": "1", "updated_at": "2026-09-14"})
        numbers += used
    s.create_numbers(numbers)
    old_columns = [c for c in COLUMNS["hypotheses"] if c not in HYPOTHESIS_COLUMNS_1_9] + list(SYSTEM_COLUMNS)
    backend.create_sheet(SHEETS["hypotheses"], old_columns)
    backend.write_rows(SHEETS["hypotheses"], [(None, row) for row in rows])
    return s


def test_old_registry_reads_and_old_launch_keeps_working(backend):
    old = [candidate("H-002"), legacy(launched())]
    s = old_registry(backend, old)
    assert not set(HYPOTHESIS_COLUMNS_1_9) & set(backend.header(SHEETS["hypotheses"]))
    assert s.read("hypotheses") == old
    # Повтор той же записи прежней версии ничего не пишет: пустые новые поля — пустые ячейки.
    assert [r.rows_written for r in s.create(candidate("H-002"))] == [0, 0]
    # Старый запуск замеряется как прежде; колонки 1.9.0 дописываются в конец шапки.
    s.add_measurement("H-001", share(1640))
    stored = {h.id: h for h in s.read("hypotheses")}
    assert stored["H-001"].status is HStatus.MEASURED and is_legacy_card(stored["H-001"])
    assert backend.header(SHEETS["hypotheses"])[-len(HYPOTHESIS_COLUMNS_1_9):] == list(HYPOTHESIS_COLUMNS_1_9)
    # Кандидат прежней версии запускается уже по правилам 1.9.0.
    s.declare_expectation("H-002", gated().noise_share, 0.0045, "абсолютное")
    with pytest.raises(GuardViolation, match="защитная метрика"):
        s.update_status("H-002", HStatus.IN_TEST, **{**LAUNCH, "guard_metric": "", "guard_threshold": None})
    s.update_status("H-002", HStatus.IN_TEST, **LAUNCH)
    assert {h.id: h.status for h in s.read("hypotheses")}["H-002"] is HStatus.IN_TEST


# --- команда реестра ---

def test_command_launch_takes_new_fields_from_json(tmp_path):
    gated_in_folder(tmp_path)
    code, lines = call(tmp_path, "hypothesis", id="H-001", status="в тесте", json=launch_json())
    assert code == 0, lines[-1]


@pytest.mark.parametrize("changes, words", [
    ({"stop_criteria": []}, "критерии остановки"),
    ({"guard_metric": ""}, "защитная метрика"),
    ({"guard_threshold": None}, "защитная метрика"),
    ({"baseline_missing": True}, "требует замера до запуска"),
], ids=["нет критериев остановки", "нет защитной метрики", "нет порога защитной", "нет исходного замера"])
def test_command_launch_refuses_incomplete_card(tmp_path, changes, words):
    gated_in_folder(tmp_path)
    code, lines = call(tmp_path, "hypothesis", id="H-001", status="в тесте", json=launch_json(**changes))
    assert code == 1 and "[страж 7]" in lines[-1] and words in lines[-1], lines[-1]


def test_command_refuses_stop_criteria_given_as_a_string(tmp_path):
    """Строка разобралась бы по буквам — каждая буква стала бы критерием остановки."""
    gated_in_folder(tmp_path)
    code, lines = call(tmp_path, "hypothesis", id="H-001", status="в тесте",
                       json=launch_json(stop_criteria="заявок нет неделю"))
    assert code == 1 and "[страж 9]" in lines[-1] and "список строк" in lines[-1]


def test_gate_hint_names_the_new_launch_fields(tmp_path):
    md_store(tmp_path).create_numbers([FACT, EFFECT, EFFECT_RUB, NOISE_SHARE])
    call(tmp_path, "hypothesis", id="H-001", status="candidate", json=card_json())
    code, lines = call(tmp_path, "gate", id="H-001",
                       json=json.dumps({"share_id": number_id(NOISE_SHARE), "change": 0.0045,
                                        "kind": "абсолютное"}, ensure_ascii=False))
    assert code == 0, lines[-1]
    hint = " ".join(lines)
    assert all(name in hint for name in ("guard_metric", "guard_threshold", "stop_criteria", "baseline_missing"))
