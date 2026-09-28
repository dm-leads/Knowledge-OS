"""Подкоманда `measure` и запуск теста (задача 7б.5): перевод в «в тесте» с карточкой запуска и замер по окну.

Дыра, найденная при разборе 7б.5: перевод статуса в команде звал `update_status(id, статус)` без полей, а переход в
«в тесте» требует десяти полей карточки запуска (`LAUNCH_FIELDS`). То есть командой невозможно было запустить тест —
и замерять было нечего. Здесь это закрывается: поля запуска идут из `--json` тем же ключом, что и карточка заведения.

Правила замера проверены тестами ядра (`test_measure_and_state.py`): та же доля, что у гейта, объявленное окно, дата
съёма не раньше конца окна, попадание в порог считает код. Здесь — что команда зовёт ядро, подаёт замер номером
записанного числа и честно печатает итог.
"""
import json
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

from growth_engine.core.arithmetic import ratio
from growth_engine.core.number import Status
from growth_engine.core.storage import RegistryStore, number_id
from growth_engine.registry import SUBCOMMANDS, run
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.helpers import (CARD, CFG, EFFECT, EFFECT_RUB, FACT, FORMULA, LAUNCH, MEASURED_ON,
                                         NOISE_SHARE, TEST_WINDOW, UNIT, n)

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = str(FIXTURES / "config_minimal.yaml")
TODAY = date(2026, 9, 15)

# Два замера одного окна нужны для двух исходов, но значение в номер числа не входит: метрика, кабинет, поток,
# период, съём, источник и знаменатель у них совпали бы, и хранилище справедливо остановило бы запись стражем 13
# («у разных расчётов одной метрики, периода и съёма часть „запрос“ источника должна называть расчёт»). Поэтому
# расчёты названы в источнике — так их и различает контракт.
def share(leads, calculation):
    return ratio(n("leads", leads, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON,
                   source=f"C:analytics-x:{calculation}"),
                 n("visits", 101146, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON,
                   source=f"C:analytics-x:{calculation}"), "cr1", CFG)


# Замер окна теста: CR1 веб 1,621% против 1,560% у гейта — +0,061 п.п. при пороге 0,30 п.п. (проверено кодом).
MEASURED_SHARE = share(1640, "замер окна теста")
# Замер выше порога: 1 895 заявок — 1,874%, то есть +0,313 п.п. (проверено кодом).
BIG_SHARE = share(1895, "замер окна теста, вариант выше порога")


def store(folder) -> RegistryStore:
    return RegistryStore(MarkdownBackend(Path(folder)), allowed_domains=(), today=lambda: TODAY)


def call(folder, cmd, **changes):
    args = Namespace(cmd=cmd, config=CONFIG, out=str(folder), lark_book=None, dry_run=False, cycle="Ц-1",
                     id=None, status=None, json=None, secrets=None, source=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    return run(args, out=lines.append, today=lambda: TODAY), lines


def card_json() -> str:
    card = {"formulation": FORMULA, "unit_of_action": {"source_class": UNIT.source_class,
                                                       "coordinates": dict(UNIT.coordinates)},
            "fact_basis_id": number_id(FACT), "effect_goal_units_id": number_id(EFFECT),
            "effect_rub_id": number_id(EFFECT_RUB)}
    card.update({name: CARD[name] for name in ("cycle_id", "business_task", "tree_branch", "model_lever",
                                               "mechanic", "main_metric", "owner")})
    return json.dumps(card, ensure_ascii=False)


def launch_json(**changes) -> str:
    """Карточка запуска: десять полей контракта; даты — строками, кортеж RAT — списком."""
    fields = {"zone": LAUNCH["zone"], "change": LAUNCH["change"], "start_date": LAUNCH["start_date"].isoformat(),
              "window_days": LAUNCH["window_days"], "expected_n": LAUNCH["expected_n"],
              "threshold": LAUNCH["threshold"], "failure_criterion": LAUNCH["failure_criterion"],
              "rat": list(LAUNCH["rat"]), "owner": CARD["owner"], "main_metric": CARD["main_metric"]}
    fields.update(changes)
    return json.dumps(fields, ensure_ascii=False)


def gated(folder, hid="H-001"):
    """Кандидат после гейта шума: числа записаны, карточка заведена, ожидание объявлено."""
    s = store(folder)
    s.create_numbers([FACT, EFFECT, EFFECT_RUB, NOISE_SHARE, MEASURED_SHARE, BIG_SHARE])
    call(folder, "hypothesis", id=hid, status="candidate", json=card_json())
    call(folder, "gate", id=hid, json=json.dumps({"share_id": number_id(NOISE_SHARE), "change": 0.004,
                                                  "kind": "абсолютное"}, ensure_ascii=False))
    return s


def launched(folder, hid="H-001", **changes):
    gated(folder, hid)
    return call(folder, "hypothesis", id=hid, status="в тесте", json=launch_json(**changes))


# --- запуск теста: закрытие дыры 7б.2 ---

def test_launch_needs_the_launch_card(tmp_path):
    """Переход в «в тесте» требует десяти полей: без них команда отказывает стражем 7, а не пишет полуфабрикат."""
    gated(tmp_path)
    code, lines = call(tmp_path, "hypothesis", id="H-001", status="в тесте", json=json.dumps({}, ensure_ascii=False))
    assert code == 1 and "[страж 7]" in lines[-1]


def test_launch_with_full_card_moves_to_in_test(tmp_path):
    code, lines = launched(tmp_path)
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-001")
    assert stored.status.value == "в тесте" and stored.threshold == LAUNCH["threshold"]
    assert stored.start_date == LAUNCH["start_date"] and stored.rat == tuple(LAUNCH["rat"])


def test_launch_cannot_redeclare_what_the_gate_declared(tmp_path):
    """Страж 5: ожидание и порог шума объявляет гейт до запуска — при запуске их переписать нельзя."""
    gated(tmp_path)
    code, lines = call(tmp_path, "hypothesis", id="H-001", status="в тесте",
                       json=launch_json(expected_delta=0.02))
    assert code == 1 and "[страж 5]" in lines[-1]


# --- замер ---

def test_measure_is_a_writing_subcommand():
    assert SUBCOMMANDS["measure"].writes is True


def test_measurement_below_threshold_says_not_in_threshold(tmp_path):
    """Известный ответ фикстуры: +0,06 п.п. при пороге 0,30 п.п. — не в пороге, и это решает код."""
    launched(tmp_path)
    code, lines = call(tmp_path, "measure", id="H-001",
                       json=json.dumps({"measured_id": number_id(MEASURED_SHARE)}, ensure_ascii=False))
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-001")
    assert stored.status.value == "замер" and stored.in_threshold is False
    assert "не в пороге" in "\n".join(lines)


def test_measurement_above_threshold_is_in_threshold(tmp_path):
    launched(tmp_path)
    code, lines = call(tmp_path, "measure", id="H-001",
                       json=json.dumps({"measured_id": number_id(BIG_SHARE)}, ensure_ascii=False))
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-001")
    assert stored.in_threshold is True and "в пороге" in "\n".join(lines)


def test_measurement_by_id_not_by_value(tmp_path):
    """Замер — номер записанного числа: значением его задать нельзя (стражи 1 и 4)."""
    launched(tmp_path)
    code, lines = call(tmp_path, "measure", id="H-001",
                       json=json.dumps({"measured": 0.0162}, ensure_ascii=False))
    assert code == 1 and "measured_id" in lines[-1]


def test_measurement_of_unknown_number_is_guard_13(tmp_path):
    launched(tmp_path)
    code, lines = call(tmp_path, "measure", id="H-001",
                       json=json.dumps({"measured_id": "0" * 16}, ensure_ascii=False))
    assert code == 1 and "[страж 13]" in lines[-1]


def test_measurement_before_the_window_ends_is_code_1(tmp_path):
    """Замер, снятый раньше конца окна, не принимается — окно ещё не закончилось (страж 7)."""
    early = replace(MEASURED_SHARE, as_of=date(2026, 10, 1))
    s = gated(tmp_path)
    s.create_numbers([early])
    call(tmp_path, "hypothesis", id="H-001", status="в тесте", json=launch_json())
    code, lines = call(tmp_path, "measure", id="H-001",
                       json=json.dumps({"measured_id": number_id(early)}, ensure_ascii=False))
    assert code == 1 and "[страж 7]" in lines[-1]


def test_measurement_of_candidate_is_code_1(tmp_path):
    """Замер добавляется только к гипотезе «в тесте» или «отложенный замер»."""
    gated(tmp_path)
    code, lines = call(tmp_path, "measure", id="H-001",
                       json=json.dumps({"measured_id": number_id(MEASURED_SHARE)}, ensure_ascii=False))
    assert code == 1 and "[страж 7]" in lines[-1]


def test_measure_reports_rows_written(tmp_path):
    launched(tmp_path)
    _, lines = call(tmp_path, "measure", id="H-001",
                    json=json.dumps({"measured_id": number_id(MEASURED_SHARE)}, ensure_ascii=False))
    assert any("записано" in line and "прочитано" in line for line in lines)
