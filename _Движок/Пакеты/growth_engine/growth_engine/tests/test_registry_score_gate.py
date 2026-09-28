"""Подкоманды `score` и `gate` (задача 7б.3): скоринг считает и печатает, гейт шума объявляет ожидание до запуска.

Разделены не по прихоти: приоритет хранить негде — у гипотезы нет полей веры, сложности и балла, и хранилище их не
пишет, поэтому `score` только считает и печатает (читающая подкоманда). Гейт шума, наоборот, пишет: `expected_delta`,
`expected_kind`, `noise_share` и `noise_threshold` — поля гипотезы, и объявляются они до запуска (страж 7).

Правила скоринга и гейта проверены тестами ядра (`test_cycle.py`, `test_noise_gate.py` — 23 теста) и известным
ответом листа (`tests/instance/test_scoring_known_answers.py`). Здесь — что команда зовёт ядро, берёт числа по
номерам из хранилища и честно отчитывается.
"""
import json
from argparse import Namespace
from datetime import date
from pathlib import Path

from growth_engine.core.cycle import CREDIBILITY_FACTORS, EFFORT_FACTORS
from growth_engine.core.storage import RegistryStore, number_id
from growth_engine.registry import SUBCOMMANDS, run
from growth_engine.storage.markdown import MarkdownBackend
from growth_engine.tests.helpers import (CARD, EFFECT, EFFECT_RUB, FACT, FORMULA, NOISE_SHARE, UNIT)

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = str(FIXTURES / "config_minimal.yaml")
TODAY = date(2026, 9, 15)

# Известный ответ листа «_Скоринг гипотез» (снимок 14.09.2026), H1: сложность 2,0 — вера 0,933.
H1_EFFORT = dict(zip(EFFORT_FACTORS, (2, 2, 2, 3, 1)))
H1_CREDIBILITY = dict(zip(CREDIBILITY_FACTORS, (5, 5, 4, 5, 4, 5)))


def store(folder) -> RegistryStore:
    return RegistryStore(MarkdownBackend(Path(folder)), allowed_domains=(), today=lambda: TODAY)


def call(folder, cmd, **changes):
    args = Namespace(cmd=cmd, config=CONFIG, out=str(folder), lark_book=None, dry_run=False, cycle="Ц-1",
                     id=None, status=None, json=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    return run(args, out=lines.append, today=lambda: TODAY), lines


def card_json(**changes) -> str:
    card = {"formulation": FORMULA, "unit_of_action": {"source_class": UNIT.source_class,
                                                       "coordinates": dict(UNIT.coordinates)},
            "fact_basis_id": number_id(FACT), "effect_goal_units_id": number_id(EFFECT),
            "effect_rub_id": number_id(EFFECT_RUB)}
    card.update({name: CARD[name] for name in ("cycle_id", "business_task", "tree_branch", "model_lever",
                                               "mechanic", "main_metric", "owner")})
    card.update(changes)
    return json.dumps(card, ensure_ascii=False)


def candidate(folder, hid="H-001"):
    """Кандидат в хранилище: числа записаны движком экономики, карточка на них ссылается."""
    s = store(folder)
    s.create_numbers([FACT, EFFECT, EFFECT_RUB, NOISE_SHARE])
    call(folder, "hypothesis", id=hid, status="candidate", json=card_json())
    return s


# --- score: считает и печатает ---

def test_score_is_a_reading_subcommand():
    """Приоритет хранить негде: у гипотезы нет полей веры, сложности и балла — значит подкоманда не пишет."""
    assert SUBCOMMANDS["score"].writes is False


def test_score_reproduces_the_sheet_known_answer(tmp_path):
    """H1 листа «_Скоринг гипотез»: сложность 2,0 и вера 0,933 — те же числа даёт код движка."""
    candidate(tmp_path)
    code, lines = call(tmp_path, "score", id="H-001",
                       json=json.dumps({"вера": H1_CREDIBILITY, "сложность": H1_EFFORT}, ensure_ascii=False))
    assert code == 0, lines[-1]
    text = "\n".join(lines)
    assert "сложность 2" in text and "вера 0,933" in text


def test_score_without_effect_in_rub_says_priority_is_not_computed(tmp_path):
    """Страж 8: без эффекта в ₽ из модели балл не считается — команда говорит это, а не печатает ноль."""
    s = store(tmp_path)
    s.create_numbers([FACT, EFFECT])
    call(tmp_path, "hypothesis", id="H-100", json=json.dumps({"formulation": FORMULA}, ensure_ascii=False))
    code, lines = call(tmp_path, "score", id="H-100",
                       json=json.dumps({"вера": H1_CREDIBILITY, "сложность": H1_EFFORT}, ensure_ascii=False))
    assert code == 0 and "приоритет не считается" in "\n".join(lines)


def test_score_writes_nothing(tmp_path):
    """Читающая подкоманда не меняет хранилище: файлы после вызова те же."""
    candidate(tmp_path)
    before = {p.name: p.read_text(encoding="utf-8") for p in Path(tmp_path).iterdir()}
    call(tmp_path, "score", id="H-001",
         json=json.dumps({"вера": H1_CREDIBILITY, "сложность": H1_EFFORT}, ensure_ascii=False))
    after = {p.name: p.read_text(encoding="utf-8") for p in Path(tmp_path).iterdir()}
    assert before == after


def test_score_with_missing_subfactor_is_code_1(tmp_path):
    """Подфактор пропущен или лишний — ошибка: вера и сложность считаются по объявленному составу."""
    candidate(tmp_path)
    broken = {name: 3 for name in list(CREDIBILITY_FACTORS)[:-1]}
    code, lines = call(tmp_path, "score", id="H-001",
                       json=json.dumps({"вера": broken, "сложность": H1_EFFORT}, ensure_ascii=False))
    assert code == 1 and "вера" in lines[-1]


def test_score_of_absent_hypothesis_is_code_1(tmp_path):
    candidate(tmp_path)
    code, lines = call(tmp_path, "score", id="H-404",
                       json=json.dumps({"вера": H1_CREDIBILITY, "сложность": H1_EFFORT}, ensure_ascii=False))
    assert code == 1 and "H-404" in lines[-1]


# --- gate: объявляет ожидание до запуска ---

def test_gate_is_a_writing_subcommand():
    assert SUBCOMMANDS["gate"].writes is True


def test_expected_change_above_noise_keeps_the_candidate(tmp_path):
    """Ожидание выше порога шума — гипотеза остаётся кандидатом, порог записан в её поля."""
    candidate(tmp_path)
    code, lines = call(tmp_path, "gate", id="H-001",
                       json=json.dumps({"share_id": number_id(NOISE_SHARE), "change": 0.004,
                                        "kind": "абсолютное"}, ensure_ascii=False))
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-001")
    assert stored.status.value == "candidate" and stored.noise_threshold is not None
    assert stored.expected_delta == 0.004 and stored.expected_kind == "абсолютное"


def test_expected_change_below_noise_goes_to_research_by_code(tmp_path):
    """Страж 5: ожидание меньше порога шума — `research` решает код, а не агент, и называет причину."""
    candidate(tmp_path)
    code, lines = call(tmp_path, "gate", id="H-001",
                       json=json.dumps({"share_id": number_id(NOISE_SHARE), "change": 0.0001,
                                        "kind": "абсолютное"}, ensure_ascii=False))
    assert code == 0, lines[-1]
    stored, = store(tmp_path).read("hypotheses", id="H-001")
    assert stored.status.value == "research" and "меньше порога шума" in stored.status_reason


def test_gate_reports_rows_written(tmp_path):
    candidate(tmp_path)
    _, lines = call(tmp_path, "gate", id="H-001",
                    json=json.dumps({"share_id": number_id(NOISE_SHARE), "change": 0.004,
                                     "kind": "абсолютное"}, ensure_ascii=False))
    assert any("записано" in line and "прочитано" in line for line in lines)


def test_gate_prints_the_threshold_ready_for_the_launch_card(tmp_path):
    """Дефект ворот 7б: связь «порог гейта → поле `threshold` карточки запуска» нигде не была видна.

    Гейт печатает порог строкой числа — в процентах, а карточка запуска принимает долю. Агент вынужден был сам
    переводить проценты в долю и угадывать, что это одно и то же число. Команда называет готовое значение.
    """
    candidate(tmp_path)
    code, lines = call(tmp_path, "gate", id="H-001",
                       json=json.dumps({"share_id": number_id(NOISE_SHARE), "change": 0.004,
                                        "kind": "абсолютное"}, ensure_ascii=False))
    assert code == 0, lines[-1]
    # Признак — явная пометка в начале строки, а не слово «threshold» где угодно: первая версия теста нашла его в
    # пути временной папки pytest и прошла бы, ничего не проверив.
    hint = [line.strip() for line in lines if line.strip().startswith("для карточки запуска")]
    assert hint, "гейт не назвал значение для поля threshold карточки запуска"
    stored, = store(tmp_path).read("hypotheses", id="H-001")
    assert f"{stored.noise_threshold.value:.6f}".rstrip("0") in hint[-1].replace(",", "."), hint[-1]


def test_gate_needs_a_declared_kind_of_change(tmp_path):
    """Вид изменения объявляется явно (страж 9): «на 10%» без вида — абсолютное или относительное, неизвестно."""
    candidate(tmp_path)
    code, lines = call(tmp_path, "gate", id="H-001",
                       json=json.dumps({"share_id": number_id(NOISE_SHARE), "change": 0.004}, ensure_ascii=False))
    assert code == 1 and "вид изменения" in lines[-1]


def test_gate_share_must_be_stored_number(tmp_path):
    """Доля для порога шума — номер записанного числа: значением её задать нельзя (стражи 1 и 4)."""
    candidate(tmp_path)
    code, lines = call(tmp_path, "gate", id="H-001",
                       json=json.dumps({"share_id": "0" * 16, "change": 0.004, "kind": "абсолютное"},
                                       ensure_ascii=False))
    assert code == 1 and "[страж 13]" in lines[-1]
