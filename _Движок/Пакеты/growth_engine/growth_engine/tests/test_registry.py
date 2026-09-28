from dataclasses import replace

import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Status
from growth_engine.core.registry import Decision, HStatus, ZONE_PACKAGE, create, iterate, transition
from growth_engine.tests.helpers import CARD, EFFECT_RUB, FORMULA, LAUNCH, candidate, gated, measured, n


def research(formulation=FORMULA):
    return transition(create(id="H-001", formulation=formulation), HStatus.RESEARCH)


def test_idea_needs_only_formulation():
    assert create(id="H-001", formulation="идея").status is HStatus.IDEA


def test_create_must_start_as_idea():
    with pytest.raises(ValueError):
        create(id="H-001", formulation=FORMULA, status=HStatus.CANDIDATE)


def test_skipping_research_rejected():
    with pytest.raises(ValueError):
        transition(create(id="H-001", formulation=FORMULA), HStatus.CANDIDATE, **CARD)


# Р1 (принято 13.09.2026): владелец обязателен с перехода в candidate.
def test_candidate_without_owner_rejected():
    with pytest.raises(GuardViolation) as e:
        transition(research(), HStatus.CANDIDATE, **{**CARD, "owner": ""})
    assert e.value.guard == 6 and "owner" in str(e.value)


def test_candidate_needs_full_creation_card():
    with pytest.raises(GuardViolation, match="tree_branch"):
        transition(research(), HStatus.CANDIDATE, **{**CARD, "tree_branch": ""})


def test_candidate_needs_formula():
    with pytest.raises(GuardViolation, match="формул"):
        transition(research("поднять CR1"), HStatus.CANDIDATE, **CARD)


def test_candidate_on_no_data_fact_rejected():
    with pytest.raises(GuardViolation):
        transition(research(), HStatus.CANDIDATE, **{**CARD, "fact_basis": n("leads", None, status=Status.NO_DATA)})


def test_effect_rub_only_from_model():
    with pytest.raises(GuardViolation) as e:
        transition(research(), HStatus.CANDIDATE, **{**CARD, "effect_rub": replace(EFFECT_RUB, source="D:чат:оценка")})
    assert e.value.guard == 8


def test_effect_rub_must_be_money():
    with pytest.raises(GuardViolation) as e:
        transition(research(), HStatus.CANDIDATE, **{**CARD, "effect_rub": replace(EFFECT_RUB, unit="шт")})
    assert e.value.guard == 8


def test_conclusion_decision_must_be_canonical():
    with pytest.raises(GuardViolation) as e:
        transition(measured(), HStatus.CONCLUDED, decision="продлить", conclusion="x", knowledge_row="K-001")
    assert e.value.guard == 14


def test_in_test_without_threshold_rejected():
    with pytest.raises(GuardViolation) as e:
        transition(candidate(), HStatus.IN_TEST, **{**LAUNCH, "threshold": None})
    assert e.value.guard == 7 and "threshold" in str(e.value)


def test_in_test_with_full_launch_card():
    assert transition(gated(), HStatus.IN_TEST, **LAUNCH).status is HStatus.IN_TEST


def test_waiting_owner_only_for_package_zone():
    with pytest.raises(GuardViolation):
        transition(candidate(), HStatus.WAITING_OWNER, **LAUNCH)
    h = transition(gated(), HStatus.WAITING_OWNER, **{**LAUNCH, "zone": ZONE_PACKAGE})
    assert h.status is HStatus.WAITING_OWNER


def test_blocked_needs_source_class():
    h = research()
    with pytest.raises(GuardViolation):
        transition(h, HStatus.BLOCKED_NO_DATA)
    assert transition(h, HStatus.BLOCKED_NO_DATA, blocked_class="F").blocked_class == "F"


def test_conclusion_requires_knowledge_row():
    with pytest.raises(GuardViolation) as e:
        transition(measured(), HStatus.CONCLUDED, decision=Decision.KILL, conclusion="не сработало")
    assert e.value.guard == 14


def test_iterate_keeps_old_formulation():
    old, new = iterate(measured(), FORMULA + " (версия 2)", conclusion="эффект ниже порога",
                       knowledge_row="K-001", next_cycle_id="Ц-2")
    assert old.formulation == FORMULA and old.decision is Decision.ITERATE
    assert (new.id, new.supersedes, new.version, new.status, new.cycle_id) == \
        ("H-001.v2", "H-001", 2, HStatus.CANDIDATE, "Ц-2")
