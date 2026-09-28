"""Доказательства из диалогов (страж 10): номер факта → фрагмент для утверждения «код факта = значение за период»;
выдуманные, чужие и неполные номера вычёркиваются и перечисляются.

Номер факта — uuid строки факта речевой аналитики (схема проверена пробой 14.09.2026).
"""
from datetime import date

import pytest

from growth_engine.core.cycle import NOT_FOUND, Evidence, Fragment, Unavailable, build_evidence
from growth_engine.core.errors import GuardViolation

REAL = "0b7c3c6e-1f2a-4b8e-9d3a-5a1f2e3d4c5b"
QUIET = "7d1e9a40-2c3b-4f5d-8e6f-0a1b2c3d4e5f"
MADE_UP = "00000000-0000-4000-8000-000000000000"
AUG = (date(2026, 8, 1), date(2026, 9, 1))


def fragment(fid, quote="дорого, посмотрим другие варианты", note="", code="rejection_reason", value="цена",
             on=date(2026, 8, 14)):
    return Fragment(id=fid, channel="calls", conversation="c-1", on=on, code=code, value=value, quote=quote, note=note)


class Source:
    """Записанная база: отдаёт фрагменты только по существующим номерам и запоминает запросы."""

    def __init__(self, *fragments):
        self.rows, self.asked = {f.id: f for f in fragments}, []

    def __call__(self, ids):
        self.asked.append(list(ids))
        return [self.rows[i] for i in ids if i in self.rows]


def evidence(ids, source, **claim):
    return build_evidence(ids, source, **{"code": "rejection_reason", "value": "цена", "period": AUG, **claim})


def test_existing_number_gives_fragment_and_made_up_is_struck():
    result = evidence([REAL, MADE_UP], Source(fragment(REAL)))
    assert isinstance(result, Evidence) and [f.id for f in result.fragments] == [REAL]
    assert result.struck == ((MADE_UP, NOT_FOUND),)


@pytest.mark.parametrize("raw", [12345, "12345", "1; drop table x", True, None, REAL.replace("-", "")])
def test_number_not_in_fact_format_is_struck_without_query(raw):
    source = Source(fragment(REAL))
    result = evidence([raw], source)
    assert result.fragments == () and result.struck == ((str(raw), "не номер факта"),) and source.asked == []


def test_case_and_repeats_collapse_to_one_request():
    source = Source(fragment(REAL))
    result = evidence([REAL.upper(), f" {REAL} ", REAL], source)
    assert source.asked == [[REAL]] and len(result.fragments) == 1 and result.struck == ()


def test_fragment_without_clean_quote_is_struck():
    result = evidence([QUIET], Source(fragment(QUIET, quote="  ")))
    assert result.fragments == () and result.struck == ((QUIET, "нет очищенной цитаты"),)


def test_source_returning_unrequested_fragment_stops():
    def leaky(ids):
        return [fragment(REAL), fragment(QUIET)]

    with pytest.raises(GuardViolation) as e:
        evidence([REAL], leaky)
    assert e.value.guard == 10


def test_source_note_travels_with_fragment():
    note = "возможна привязка к соседней беседе"
    assert evidence([REAL], Source(fragment(REAL, note=note))).fragments[0].note == note


# Ревью 14.09: доказательство сверяется с утверждением — код, значение и период.
def test_fragment_of_other_claim_is_struck():
    source = Source(fragment(REAL, code="competitor", value="выбрал конкурента"),
                    fragment(QUIET, value="думает и пропал"), fragment(MADE_UP, on=date(2025, 8, 14)))
    result = evidence([REAL, QUIET, MADE_UP], source)
    reasons = dict(result.struck)
    assert result.fragments == () and "competitor" in reasons[REAL] and "думает и пропал" in reasons[QUIET]
    assert "вне периода" in reasons[MADE_UP]


def test_value_compared_without_extra_spaces():
    assert len(evidence([REAL], Source(fragment(REAL, value=" цена  "))).fragments) == 1


def test_fragment_without_conversation_date_is_struck():
    assert evidence([REAL], Source(fragment(REAL, on=None))).struck == ((REAL, "нет даты беседы"),)


def test_claim_without_code_stops():
    with pytest.raises(GuardViolation) as e:
        build_evidence([REAL], Source(fragment(REAL)), code=" ")
    assert e.value.guard == 10


# Ревью Codex 15.09: источник называет, почему не отдал фрагмент, — причина не сливается с «нет в базе».
def test_unavailable_number_keeps_source_reason():
    def source(ids):
        return [Unavailable(id=REAL, reason="код факта «x» вне белого списка конфигурации")]

    result = evidence([REAL, MADE_UP], source)
    assert result.struck == ((REAL, "код факта «x» вне белого списка конфигурации"), (MADE_UP, NOT_FOUND))


def test_unrequested_unavailable_number_stops():
    with pytest.raises(GuardViolation) as e:
        evidence([REAL], lambda ids: [Unavailable(id=QUIET, reason="нет связи с беседой")])
    assert e.value.guard == 10
