from datetime import date

import pytest

from datacore.schema.errors import RuleViolation
from datacore.schema.number import Number, Status, add

AUG = dict(period_start=date(2026, 8, 1), period_end=date(2026, 9, 1), as_of=date(2026, 9, 6))


def n(value=1.0, **kw):
    base = dict(metric="new_first_sql", level="new_first_sql", scope="s1", flow="all",
                source="C:sys:query", status=Status.FACT, value=value, **AUG)
    base.update(kw)
    return Number(**base)


def test_number_without_as_of_is_rejected_with_k2():
    with pytest.raises(RuleViolation) as e:
        n(as_of=None)
    assert e.value.code == "К2"


@pytest.mark.parametrize("field", ["metric", "level", "scope", "flow", "source"])
def test_empty_field_is_k2(field):
    with pytest.raises(RuleViolation) as e:
        n(**{field: ""})
    assert e.value.code == "К2"


def test_source_must_be_class_system_query():
    with pytest.raises(RuleViolation) as e:
        n(source="sys:query")
    assert e.value.code == "К2"
    assert n().source_class == "C" and n().source_system == "sys"


def test_no_data_is_not_zero():
    with pytest.raises(RuleViolation):
        n(status=Status.NO_DATA, value=0)
    with pytest.raises(RuleViolation):
        n(status=Status.FACT, value=None)
    assert n(status=Status.NO_DATA, value=None).value is None


def test_period_end_exclusive_and_after_start():
    with pytest.raises(RuleViolation):
        n(period_end=date(2026, 8, 1))


def test_add_same_period_two_scopes_requires_proof_k7():
    a, b = n(587, scope="s1"), n(43, scope="s2")
    with pytest.raises(RuleViolation) as e:
        add([a, b])
    assert e.value.code == "К7"
    total = add([a, b], cross_scope_proof="дедуп по контакту доказан 10.09.2026")
    assert (total.value, total.scope, total.status) == (630, "s1+s2", Status.FACT)


def test_add_different_as_of_is_k7():
    a, b = n(1, scope="s1"), n(2, scope="s2", as_of=date(2026, 9, 7))
    with pytest.raises(RuleViolation) as e:
        add([a, b], cross_scope_proof="x")
    assert e.value.code == "К7"


def test_add_different_level_is_k7():
    with pytest.raises(RuleViolation) as e:
        add([n(1), n(2, level="lead", scope="s2")], cross_scope_proof="x")
    assert e.value.code == "К7"


def test_add_adjacent_periods_same_scope_ok_but_gap_is_k7():
    jul = n(658, period_start=date(2026, 7, 1), period_end=date(2026, 8, 1))
    aug = n(630)
    total = add([jul, aug])
    assert (total.value, total.period_start, total.period_end) == (1288, date(2026, 7, 1), date(2026, 9, 1))
    jun = n(706, period_start=date(2026, 6, 1), period_end=date(2026, 7, 1))
    with pytest.raises(RuleViolation) as e:
        add([jun, aug])          # июль пропущен
    assert e.value.code == "К7"


def test_add_takes_weakest_status_and_no_data_poisons_sum():
    total = add([n(1), n(2, scope="s2", status=Status.ESTIMATE)], cross_scope_proof="x")
    assert total.status == Status.ESTIMATE
    total = add([n(1), n(None, scope="s2", status=Status.NO_DATA)], cross_scope_proof="x")
    assert (total.status, total.value) == (Status.NO_DATA, None)


def test_shares_are_not_added():
    a = n(0.5, unit="доля", denominator="visits")
    b = n(0.4, unit="доля", denominator="visits", scope="s2")
    with pytest.raises(RuleViolation) as e:
        add([a, b], cross_scope_proof="x")
    assert e.value.code == "К7"


def test_sum_does_not_repeat_the_same_note():
    """Одна и та же причина у нескольких слагаемых пишется один раз: при сложении кабинетов пояснение
    «загрузка не завершена» повторялось столько раз, сколько кабинетов (16.09.2026)."""
    common = dict(metric="revenue", level="payment", flow="all", unit="₽", as_of=date(2026, 9, 16),
                  period_start=date(2026, 4, 1), period_end=date(2026, 5, 1), source="D:x:facts.payment",
                  status=Status.ESTIMATE)
    a = Number(scope="b1", value=10.0, missing="загрузка не завершена; своё про первый кабинет", **common)
    b = Number(scope="b2", value=5.0, missing="загрузка не завершена; своё про второй кабинет", **common)
    total = add([a, b], cross_scope_proof="доказано")
    assert total.value == 15.0
    assert total.missing.count("загрузка не завершена") == 1
    assert "своё про первый кабинет" in total.missing and "своё про второй кабинет" in total.missing
