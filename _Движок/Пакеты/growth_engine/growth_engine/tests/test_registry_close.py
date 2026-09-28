"""Подкоманда `close` (задача 7б.7): закрытие цикла решением, строкой знаний и письменным вычитанием.

Цикл закрывается не словом «готово», а тремя вещами сразу (канон, шаг 10): у гипотезы есть решение
`scale / iterate / kill / research`, у решённой гипотезы — своя строка карты знаний (страж 14), и в журнале решений
цикла записано, что убрали (страж 15). Правила проверены тестами ядра (`test_artifacts.py`, `test_registry.py`);
здесь — что команда зовёт ядро, пишет все три артефакта и отчитывается числом строк.

Решение `iterate` заводит следующую версию гипотезы в новом цикле — это делает ядро (`registry.iterate`), команда его
не переписывает.
"""
import json
from argparse import Namespace
from dataclasses import replace
from datetime import date
from pathlib import Path

from growth_engine.core.arithmetic import ratio
from growth_engine.core.storage import RegistryStore, number_id
from growth_engine.registry import SUBCOMMANDS, run
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.helpers import (CARD, CFG, EFFECT, EFFECT_RUB, FACT, FORMULA, LAUNCH, MEASURED_ON,
                                         NOISE_SHARE, TEST_WINDOW, UNIT, n)

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = str(FIXTURES / "config_minimal.yaml")
TODAY = date(2026, 9, 15)

MEASURED_SHARE = ratio(n("leads", 1640, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON,
                         source="C:analytics-x:замер окна теста"),
                       n("visits", 101146, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON,
                         source="C:analytics-x:замер окна теста"), "cr1", CFG)


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


def launch_json() -> str:
    return json.dumps({"zone": LAUNCH["zone"], "change": LAUNCH["change"],
                       "start_date": LAUNCH["start_date"].isoformat(), "window_days": LAUNCH["window_days"],
                       "expected_n": LAUNCH["expected_n"], "threshold": LAUNCH["threshold"],
                       "failure_criterion": LAUNCH["failure_criterion"], "rat": list(LAUNCH["rat"]),
                       "owner": CARD["owner"], "main_metric": CARD["main_metric"]}, ensure_ascii=False)


def close_json(**changes) -> str:
    """Карточка закрытия: решение, вывод, строка знаний и запись журнала решений с вычитанием."""
    fields = {
        "decision": "kill",
        "conclusion": "эффект ниже порога",
        "knowledge": {"id": "K-001", "statement": "шаг 2 формы подбора не барьер", "verdict": "опровергнуто",
                      "on": "2026-10-20", "source": "C:analytics-x:data", "hypothesis_id": "H-001"},
        "decision_row": {"id": "D-001", "decided": "H-001 остановлена", "why": "изменение ниже порога шума",
                         "subtraction": "убрали шаг 2 формы из плана работ"},
    }
    fields.update(changes)
    return json.dumps(fields, ensure_ascii=False)


def measured(folder, hid="H-001"):
    """Гипотеза, доведённая командами до статуса «замер»: заведение → гейт → запуск → замер."""
    s = store(folder)
    s.create_numbers([FACT, EFFECT, EFFECT_RUB, NOISE_SHARE, MEASURED_SHARE])
    call(folder, "hypothesis", id=hid, status="candidate", json=card_json())
    call(folder, "gate", id=hid, json=json.dumps({"share_id": number_id(NOISE_SHARE), "change": 0.004,
                                                  "kind": "абсолютное"}, ensure_ascii=False))
    call(folder, "hypothesis", id=hid, status="в тесте", json=launch_json())
    call(folder, "measure", id=hid, json=json.dumps({"measured_id": number_id(MEASURED_SHARE)}, ensure_ascii=False))
    return s


# --- закрытие цикла ---

def test_close_is_a_writing_subcommand():
    assert SUBCOMMANDS["close"].writes is True


def test_cycle_is_closed_with_decision_knowledge_and_subtraction(tmp_path):
    measured(tmp_path)
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1", json=close_json())
    assert code == 0, lines[-1]
    s = store(tmp_path)
    hypothesis, = s.read("hypotheses", id="H-001")
    assert hypothesis.status.value == "вывод" and hypothesis.decision.value == "kill"
    assert hypothesis.knowledge_row == "K-001"
    knowledge, = s.read("knowledge")
    assert knowledge.verdict == "опровергнуто" and knowledge.hypothesis_id == "H-001"
    decision, = s.read("decisions")
    assert decision.subtraction and decision.cycle_id == "Ц-1"


def test_close_reports_rows_for_every_sheet(tmp_path):
    measured(tmp_path)
    _, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1", json=close_json())
    report = [line for line in lines if "записано" in line and "прочитано" in line]
    assert len(report) >= 3, report          # знания, решения и сама гипотеза


def test_close_without_subtraction_is_guard_15(tmp_path):
    """Страж 15: цикл не закрыт, пока письменно не сказано, что убрали."""
    measured(tmp_path)
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1", json=close_json(
        decision_row={"id": "D-001", "decided": "H-001 остановлена", "why": "ниже порога", "subtraction": "  "}))
    assert code == 1 and "[страж 15]" in lines[-1]


def test_close_without_knowledge_row_is_guard_14(tmp_path):
    """Страж 14: у решённой гипотезы обязана быть своя строка карты знаний."""
    measured(tmp_path)
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1",
                       json=close_json(knowledge={"id": "K-001", "statement": "о другой гипотезе",
                                                  "verdict": "открыто", "on": "2026-10-20",
                                                  "source": "C:analytics-x:data", "hypothesis_id": "H-777"}))
    assert code == 1 and "[страж 14]" in lines[-1]


def test_verdict_outside_canon_is_code_1(tmp_path):
    """Вердикт знания — из канона: «почти подтверждено» не принимается, и все три названы в отказе."""
    measured(tmp_path)
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1",
                       json=close_json(knowledge={"id": "K-001", "statement": "s", "verdict": "почти подтверждено",
                                                  "on": "2026-10-20", "source": "C:analytics-x:data",
                                                  "hypothesis_id": "H-001"}))
    assert code == 1 and "подтверждено" in lines[-1] and "опровергнуто" in lines[-1]


def test_decision_outside_canon_is_code_1(tmp_path):
    measured(tmp_path)
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1", json=close_json(decision="почти получилось"))
    assert code == 1 and "scale" in lines[-1] and "iterate" in lines[-1]


def test_knowledge_without_source_is_code_1(tmp_path):
    """Число без источника — не число, и знание без источника — не знание (страж 1)."""
    measured(tmp_path)
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1",
                       json=close_json(knowledge={"id": "K-001", "statement": "s", "verdict": "открыто",
                                                  "on": "2026-10-20", "source": "", "hypothesis_id": "H-001"}))
    assert code == 1 and "источник" in lines[-1]


def test_close_of_unmeasured_hypothesis_is_code_1(tmp_path):
    """Переход в «вывод» возможен только из «замер» — иначе контракт переходов отказывает."""
    s = store(tmp_path)
    s.create_numbers([FACT, EFFECT, EFFECT_RUB, NOISE_SHARE])
    call(tmp_path, "hypothesis", id="H-001", status="candidate", json=card_json())
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1", json=close_json())
    assert code == 1 and "переход" in lines[-1]


# --- iterate заводит преемника ---

def test_iterate_creates_the_next_version(tmp_path):
    """Решение iterate: прежняя гипотеза закрывается, новая версия стартует кандидатом в следующем цикле."""
    measured(tmp_path)
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1", json=close_json(
        decision="iterate", next_formulation="если убрать шаг 3 формы, то CR1 вырастет, потому что там теряется треть",
        next_cycle="Ц-2"))
    assert code == 0, lines[-1]
    s = store(tmp_path)
    closed, = s.read("hypotheses", id="H-001")
    successor, = s.read("hypotheses", id="H-001.v2")
    assert closed.decision.value == "iterate" and closed.status.value == "вывод"
    assert successor.status.value == "candidate" and successor.supersedes == "H-001"
    assert successor.cycle_id == "Ц-2" and successor.version == 2


def test_iterate_without_next_formulation_is_code_1(tmp_path):
    measured(tmp_path)
    code, lines = call(tmp_path, "close", id="H-001", cycle="Ц-1", json=close_json(decision="iterate"))
    assert code == 1 and "формулиров" in lines[-1]
