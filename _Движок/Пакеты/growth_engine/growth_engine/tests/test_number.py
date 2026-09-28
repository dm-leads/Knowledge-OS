from datetime import date, datetime

import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Number, Status


def make(**kw):
    base = dict(metric="leads", level="lead", scope="p1", flow="all",
                period_start=date(2026, 8, 1), period_end=date(2026, 9, 1),
                source="C:analytics-x:data", status=Status.FACT, value=10.0, as_of=date(2026, 9, 3))
    base.update(kw)
    return Number(**base)


def test_valid_number():
    n = make()
    assert n.value == 10.0 and n.source_class == "C" and n.source_system == "analytics-x"


@pytest.mark.parametrize("field", ["metric", "level", "scope", "flow", "source"])
def test_empty_field_rejected(field):
    with pytest.raises(GuardViolation) as e:
        make(**{field: ""})
    assert e.value.guard == 1


@pytest.mark.parametrize("source", ["analytics-x", "Z:x:y", "C::data", "C:x:"])
def test_bad_source_rejected(source):
    with pytest.raises(GuardViolation):
        make(source=source)


def test_reversed_period_rejected():
    with pytest.raises(GuardViolation):
        make(period_start=date(2026, 9, 1), period_end=date(2026, 8, 1))


def test_no_data_is_not_zero():
    with pytest.raises(GuardViolation):
        make(status=Status.NO_DATA, value=0.0)
    assert make(status=Status.NO_DATA, value=None).value is None


@pytest.mark.parametrize("value", ["10", float("nan"), float("inf"), True])
def test_value_must_be_finite_number(value):
    with pytest.raises(GuardViolation):
        make(value=value)


@pytest.mark.parametrize("segment", ["seo", "=seo", "marker_level_1="])
def test_segment_must_be_dimension_equals_value(segment):
    with pytest.raises(GuardViolation):
        make(segment=segment)
    assert make(segment="marker_level_1=seo").segment == "marker_level_1=seo"


def test_empty_value_requires_no_data():
    with pytest.raises(GuardViolation):
        make(value=None)


def test_share_requires_denominator():
    with pytest.raises(GuardViolation) as e:
        make(unit="доля", value=0.5)
    assert e.value.guard == 9


# П3: без даты съёма дрейф задним числом (Т1, Т6) не отличить от настоящего изменения.
@pytest.mark.parametrize("as_of", [None, "2026-09-03", datetime(2026, 9, 3, 12, 0)])
def test_fetch_date_is_required_day(as_of):
    with pytest.raises(GuardViolation) as e:
        make(as_of=as_of)
    assert e.value.guard == 1
