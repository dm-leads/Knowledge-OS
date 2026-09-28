import copy
from pathlib import Path

import pytest
import yaml

from growth_engine.core.config import load_config, parse_config
from growth_engine.core.errors import GuardViolation

FIXTURE = Path(__file__).parent / "fixtures" / "config_minimal.yaml"
RAW = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_loads():
    cfg = load_config(FIXTURE)
    assert cfg.goal_metric == "sales_entry" and cfg.goal_window_months == 3
    assert cfg.rule("sales_entry").cross_scope_additive is True
    assert cfg.rule("sales").period_additive is False


def test_summable_flag_parsed():
    cfg = load_config(FIXTURE)
    assert cfg.rule("avg_check").summable is False and cfg.rule("leads").summable is True


def test_exception_without_proof_rejected():
    raw = copy.deepcopy(RAW)
    raw["metrics"]["leads"]["cross_scope_additive"] = True
    with pytest.raises(ValueError, match="без ссылки на проверку"):
        parse_config(raw)


def test_unknown_business_form_value_rejected():
    raw = copy.deepcopy(RAW)
    raw["business_form"]["purchase"] = "подписка"
    with pytest.raises(ValueError, match="форма бизнеса"):
        parse_config(raw)


def test_metric_with_undeclared_level_rejected():
    raw = copy.deepcopy(RAW)
    raw["metrics"]["leads"]["level"] = "unknown"
    with pytest.raises(ValueError, match="уровень"):
        parse_config(raw)


def test_goal_metric_must_be_declared():
    raw = copy.deepcopy(RAW)
    raw["goal"]["metric"] = "revenue"
    with pytest.raises(ValueError, match="цель"):
        parse_config(raw)


def test_undeclared_metric_is_guard_3():
    with pytest.raises(GuardViolation) as e:
        load_config(FIXTURE).rule("revenue")
    assert e.value.guard == 3


# Горизонт когорты — цикл сделки (П2, Т6): необязателен, но без доказательства не принимается.
def test_cohort_horizon_is_optional_and_needs_proof():
    assert load_config(FIXTURE).cohort_horizon_days is None
    raw = copy.deepcopy(RAW)
    raw["economy"] = {"cohort_horizon_days": 90}
    with pytest.raises(ValueError, match="без доказательства"):
        parse_config(raw)
    raw["economy"]["cohort_horizon_proof"] = "перцентиль цикла сделки на зрелых когортах"
    assert parse_config(raw).cohort_horizon_days == 90


@pytest.mark.parametrize("days", [0, -30, 1.5, "90", True])
def test_cohort_horizon_must_be_positive_whole_days(days):
    raw = copy.deepcopy(RAW)
    raw["economy"] = {"cohort_horizon_days": days, "cohort_horizon_proof": "доказательство"}
    with pytest.raises(ValueError):
        parse_config(raw)
