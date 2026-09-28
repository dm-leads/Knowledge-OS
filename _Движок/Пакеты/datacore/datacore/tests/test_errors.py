import pytest

from datacore.schema.errors import RULE_CODES, RuleViolation


def test_rule_violation_carries_code_and_prints_it():
    e = RuleViolation("К4", "сделка без контакта")
    assert e.code == "К4"
    assert e.severity == RuleViolation.STRUCTURE
    assert str(e) == "[К4] сделка без контакта"


def test_unknown_code_is_rejected():
    with pytest.raises(ValueError):
        RuleViolation("К99", "нет такого правила")


def test_ten_codes_exist():
    assert RULE_CODES == tuple(f"К{i}" for i in range(1, 11))
