"""Каталог операций (шаг 4, К1): контракт и код вместе — у каждой подкоманды контракт, эффекты сходятся с флагами."""
import json
import re
from pathlib import Path

from content_engine.cli import build_parser, main
from content_engine.core import contract

TESTS = Path(__file__).parent


def _test_names() -> set[str]:
    return {m for f in TESTS.glob("test_*.py") for m in re.findall(r"^def (test_\w+)", f.read_text(encoding="utf-8"),
                                                                   re.M)}


def test_every_leaf_has_contract_and_no_extra():
    cat = contract.catalog()
    assert cat["missing"] == [] and cat["extra"] == []
    assert len(cat["operations"]) == len(contract.leaves(build_parser()))


def test_contracts_valid_and_ids_unique():
    cat = contract.catalog()
    problems = [p for op in cat["operations"]
                for p in contract.validate(op["command"], {k: v for k, v in op.items()
                                                           if k not in ("command", "input_schema")})]
    assert problems == []
    ids = [op["operation_id"] for op in cat["operations"]]
    assert len(ids) == len(set(ids))


def test_paid_effect_iff_confirm_spend_flag():
    parsers = dict(contract.leaves(build_parser()))
    for op in contract.catalog()["operations"]:
        paid = any(e["type"] == "paid_external_call" for e in op["effects"])
        assert paid == ("--confirm-spend" in contract.flags(parsers[op["command"]])), op["command"]


def test_apply_effect_iff_apply_flag():
    parsers = dict(contract.leaves(build_parser()))
    for op in contract.catalog()["operations"]:
        needs = any(e.get("requires_apply") for e in op["effects"])
        assert needs == ("--apply" in contract.flags(parsers[op["command"]])), op["command"]


def test_money_and_writes_name_existing_proof_tests():
    names = _test_names()
    for op in contract.catalog()["operations"]:
        risky = any(e["type"] in ("paid_external_call", "external_write") for e in op["effects"])
        if risky:
            assert op.get("proof"), f"{op['command']}: деньги или запись вовне без доказательства"
        missing = [t for t in op.get("proof") or [] if t not in names]
        assert missing == [], f"{op['command']}: нет тестов {missing}"


def test_paid_operations_proved_by_behaviour_not_flags():
    """О3: тест разбора флагов не доказывает бюджет-гейт — у платной операции есть тест поведения."""
    for op in contract.catalog()["operations"]:
        if any(e["type"] == "paid_external_call" for e in op["effects"]):
            assert set(op.get("proof") or []) - {"test_serp_commands_take_budget_flags"}, op["command"]


def test_rollback_right_is_profile_keys_from_code():
    """О3: право отката в контракте — ключи профиля, ровно те, что могут запросить действия отката."""
    from content_engine.core import restore
    op = next(o for o in contract.catalog()["operations"] if o["command"] == "publish rollback")
    assert sorted(op["write"]["right"]) == sorted(restore.right_text(r) for r in restore.RIGHTS)
    assert contract.validate("x", dict(op, write=dict(op["write"], right="та же ступень")))


def test_profile_commands_name_reservation_release():
    """Команда с профилем открывает инстанс с записью — истёкшие брони освобождаются; исключения названы с причиной."""
    read_only = {"status"}                                  # open_instance(read_only=True)
    own_open = {"profile check", "profile migrate"}         # профиль читается сам, инстанс не открывается
    parsers = dict(contract.leaves(build_parser()))
    for op in contract.catalog()["operations"]:
        if "--profile" not in contract.flags(parsers[op["command"]]) or op["command"] in read_only | own_open:
            continue
        assert "истёкшие брони" in op["side_writes"], op["command"]
        assert any(e["type"] == "filesystem_write" and not e.get("requires_apply") for e in op["effects"]), op["command"]


def test_next_points_to_existing_commands():
    commands = {op["command"] for op in contract.catalog()["operations"]}
    for op in contract.catalog()["operations"]:
        assert set(op["next"]) <= commands, op["command"]


def test_catalog_command_json_and_md(capsys):
    assert main(["catalog", "--json"]) == 0
    cat = json.loads(capsys.readouterr().out)
    take = next(op for op in cat["operations"] if op["command"] == "queue take")
    assert "id" in take["input_schema"]["positional"] and "confirm-spend" in take["input_schema"]["properties"]
    assert cat["catalog_schema_version"] == contract.CATALOG_SCHEMA_VERSION
    assert main(["catalog", "--md"]) == 0
    md = capsys.readouterr().out
    assert md.count("\n## `") == len(cat["operations"]) and "<!--" not in md
