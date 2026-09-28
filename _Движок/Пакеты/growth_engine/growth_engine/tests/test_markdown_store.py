import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Number, Status
from growth_engine.core.storage import WriteReport
from growth_engine.storage.markdown import MarkdownStore
from growth_engine.tests.helpers import AS_OF, MAY, n


def numbers():
    share = Number(metric="cr1", level="lead/visit", scope="p1", flow="web", period_start=MAY[0], period_end=MAY[1],
                   as_of=AS_OF,
                   source="C:analytics-x:data", status=Status.FACT, value=1578 / 101146,
                   denominator="visits=101146", unit="доля")
    money = Number(metric="revenue", level="money", scope="p1", flow="web", period_start=MAY[0], period_end=MAY[1],
                   as_of=AS_OF,
                   source="C:analytics-x:data", status=Status.PROXY, value=12654556.0, unit="₽",
                   missing="деньги по потоку | зависят от атрибуции")
    channel = Number(metric="leads", level="lead", scope="p1", flow="web", period_start=MAY[0], period_end=MAY[1],
                     as_of=AS_OF,
                     source="C:analytics-x:data", status=Status.FACT, value=1981.0, segment="marker_level_1=seo")
    return [n("sales_entry", 587, "p1"), n("leads", None, "p2", status=Status.NO_DATA), share, money, channel]


def test_round_trip_reports_rows(tmp_path):
    report = MarkdownStore(tmp_path).write_numbers("срез", numbers())
    assert (report.rows_written, report.rows_read_back) == (5, 5)
    assert MarkdownStore(tmp_path).read_numbers("срез") == numbers()


def test_read_back_mismatch_is_error(tmp_path):
    class LossyStore(MarkdownStore):
        def _render(self, artifact, items):
            return super()._render(artifact, items[:-1])

    with pytest.raises(GuardViolation) as e:
        LossyStore(tmp_path).write_numbers("срез", numbers())
    assert e.value.guard == 13


def test_report_with_different_counts_is_error():
    with pytest.raises(GuardViolation):
        WriteReport("срез", 4, 3)
