"""Подкоманды `rat` и `evidence` (задача 7б.4): порядок допущений и доказательства по номерам фактов.

Обе читающие: порядок допущений и собранные фрагменты никуда не пишутся — допущения попадают в поле `rat` гипотезы
при запуске теста, а не здесь.

`evidence` — единственная подкоманда, которой нужен живой источник (база диалогов). Адаптер строится фабрикой по
конфигурации и подменяется в тестах так же, как мост хранилища: параметром `adapter_factory`. Правила сбора
доказательств проверены тестами ядра (`test_evidence.py`) — здесь проверяется, что команда зовёт ядро, требует
секреты только там, где они нужны, и печатает вычеркнутые номера с причинами, а не их число.
"""
import json
from argparse import Namespace
from datetime import date
from pathlib import Path

from growth_engine.core.cycle import Fragment, Unavailable
from growth_engine.registry import SUBCOMMANDS, run

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = str(FIXTURES / "config_minimal.yaml")
TODAY = date(2026, 9, 15)

REAL = "0b7c3c6e-1f2a-4b8e-9d3a-5a1f2e3d4c5b"
QUIET = "7d1e9a40-2c3b-4f5d-8e6f-0a1b2c3d4e5f"
MADE_UP = "00000000-0000-4000-8000-000000000000"
AUG = ["2026-08-01", "2026-09-01"]


def fragment(fid=REAL, code="rejection_reason", value="цена", on=date(2026, 8, 14)):
    return Fragment(id=fid, channel="calls", conversation="c-1", on=on, code=code, value=value,
                    quote="дорого, посмотрим другие варианты")


def source(*items):
    """Записанная база: отдаёт только то, о чём просили, — как настоящий адаптер."""
    rows = {item.id: item for item in items}

    class Adapter:
        def fragments(self, ids):
            return [rows[i] for i in ids if i in rows]

    return lambda *args, **kwargs: Adapter()


def call(cmd, adapter=None, **changes):
    args = Namespace(cmd=cmd, config=CONFIG, out=None, lark_book=None, dry_run=False, cycle="Ц-1",
                     id=None, status=None, json=None, secrets=None, source=None)
    for key, value in changes.items():
        setattr(args, key, value)
    lines = []
    code = run(args, out=lines.append, today=lambda: TODAY,
               adapter_factory=adapter or source(fragment()))
    return code, lines


# --- rat ---

def test_rat_is_a_reading_subcommand():
    assert SUBCOMMANDS["rat"].writes is False


def test_removable_assumption_goes_first(tmp_path):
    """Канон, шаг 8: сначала то, что можно убрать, — убрать дешевле любой проверки."""
    code, lines = call("rat", json=json.dumps({"assumptions": [
        {"statement": "сегмент платит за монтаж отдельно", "p_wrong": 0.9, "cost_of_error": 5000,
         "cost_of_check": 10},
        {"statement": "для заявки нужен звонок менеджера", "p_wrong": 0.1, "cost_of_error": 10,
         "cost_of_check": 100, "removable": True},
    ]}, ensure_ascii=False))
    assert code == 0, lines[-1]
    order = [line for line in lines if line.startswith(("1.", "2."))]
    assert order[0].startswith("1.") and "для заявки нужен звонок" in order[0]
    assert "убрать" in order[0]


def test_cheaper_check_goes_first_at_equal_risk(tmp_path):
    code, lines = call("rat", json=json.dumps({"assumptions": [
        {"statement": "посетители бросают форму на шаге 2", "p_wrong": 0.4, "cost_of_error": 900,
         "cost_of_check": 300},
        {"statement": "менеджер перезванивает в течение часа", "p_wrong": 0.4, "cost_of_error": 900,
         "cost_of_check": 30},
    ]}, ensure_ascii=False))
    assert code == 0
    first = next(line for line in lines if line.startswith("1."))
    assert "менеджер перезванивает" in first


def test_rat_prints_priority_of_each_assumption():
    """Формула упорядочивает, а не оценивает: приоритет виден числом у каждой строки."""
    _, lines = call("rat", json=json.dumps({"assumptions": [
        {"statement": "допущение", "p_wrong": 0.5, "cost_of_error": 1000, "cost_of_check": 100}]},
        ensure_ascii=False))
    assert any("5" in line for line in lines if line.startswith("1."))


def test_rat_without_assumptions_is_code_1():
    code, lines = call("rat", json=json.dumps({"assumptions": []}, ensure_ascii=False))
    assert code == 1 and "допущен" in lines[-1]


def test_probability_outside_unit_interval_is_code_1():
    code, lines = call("rat", json=json.dumps({"assumptions": [
        {"statement": "допущение", "p_wrong": 1.4, "cost_of_error": 1000, "cost_of_check": 100}]},
        ensure_ascii=False))
    assert code == 1 and "[страж 9]" in lines[-1]


# --- evidence ---

def test_evidence_is_a_reading_subcommand():
    assert SUBCOMMANDS["evidence"].writes is False


def test_made_up_number_is_struck_with_reason():
    """Выдуманный номер вычёркивается и называется вместе с причиной — не молча пропадает."""
    code, lines = call("evidence", secrets="секреты.env", json=json.dumps(
        {"ids": [REAL, MADE_UP], "code": "rejection_reason", "value": "цена", "period": AUG}, ensure_ascii=False))
    assert code == 0, lines[-1]
    text = "\n".join(lines)
    assert MADE_UP in text and "нет в базе диалогов" in text
    assert "фрагментов 1" in text and "вычеркнуто 1" in text


def test_unavailable_number_keeps_the_source_reason():
    """Номер, который база знает, но не отдаёт, приходит с её причиной — команда её не заменяет своей."""
    adapter = source(fragment(), Unavailable(id=QUIET, reason="код вне белого списка"))
    code, lines = call("evidence", adapter=adapter, secrets="секреты.env", json=json.dumps(
        {"ids": [REAL, QUIET], "code": "rejection_reason", "value": "цена", "period": AUG}, ensure_ascii=False))
    assert code == 0 and "код вне белого списка" in "\n".join(lines)


def test_quotes_are_not_printed():
    """ПДн и цитаты не печатаются: в выводе — номера, коды и причины, но не текст беседы."""
    _, lines = call("evidence", secrets="секреты.env", json=json.dumps(
        {"ids": [REAL], "code": "rejection_reason", "value": "цена", "period": AUG}, ensure_ascii=False))
    assert "дорого, посмотрим" not in "\n".join(lines)


def test_evidence_without_secrets_is_code_1():
    """Секреты нужны только этой подкоманде — и она требует их сама, а не заставляет остальные девять."""
    code, lines = call("evidence", json=json.dumps(
        {"ids": [REAL], "code": "rejection_reason", "period": AUG}, ensure_ascii=False))
    assert code == 1 and "--secrets" in lines[-1]


def test_evidence_without_claim_is_code_1():
    """Страж 10: доказательство собирается под утверждение «код факта = значение за период», а не «вообще»."""
    code, lines = call("evidence", secrets="секреты.env", json=json.dumps(
        {"ids": [REAL]}, ensure_ascii=False))
    assert code == 1 and "код факта" in lines[-1]


def test_evidence_nothing_proved_says_so():
    """Все номера вычеркнуты — так и сказано: пустой список фрагментов читается как «доказательств нет»."""
    code, lines = call("evidence", secrets="секреты.env", json=json.dumps(
        {"ids": [MADE_UP], "code": "rejection_reason", "value": "цена", "period": AUG}, ensure_ascii=False))
    assert code == 0 and "фрагментов 0" in "\n".join(lines)
