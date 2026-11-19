"""1.8.1: тест, который не стартовал, закрывается архивом — только явным признаком, с причиной и до конца окна."""
from dataclasses import replace
from datetime import date, timedelta

import pytest

from growth_engine.core.errors import GuardViolation
from growth_engine.core.registry import HStatus, Hypothesis, transition


def in_test(started_days_ago=5, window=28):
    return Hypothesis(id="H-1", formulation="если x, то y, потому что z", status=HStatus.IN_TEST,
                      start_date=date.today() - timedelta(days=started_days_ago), window_days=window)


def test_not_started_test_goes_to_archive_with_flag_and_reason():
    got = transition(in_test(), HStatus.ARCHIVE, not_started=True, status_reason="вариант не выложили")
    assert got.status is HStatus.ARCHIVE and got.status_reason.startswith("тест не стартовал:")


def test_archive_without_flag_or_reason_is_refused():
    with pytest.raises(GuardViolation, match="not_started"):
        transition(in_test(), HStatus.ARCHIVE, status_reason="вариант не выложили")
    with pytest.raises(GuardViolation, match="not_started"):
        transition(in_test(), HStatus.ARCHIVE, not_started=True)


def test_measured_or_finished_window_cannot_be_archived():
    with pytest.raises(GuardViolation, match="замер"):
        transition(replace(in_test(), measured=object()), HStatus.ARCHIVE, not_started=True, status_reason="x")
    with pytest.raises(GuardViolation, match="окно теста кончилось"):
        transition(in_test(started_days_ago=40), HStatus.ARCHIVE, not_started=True, status_reason="x")
