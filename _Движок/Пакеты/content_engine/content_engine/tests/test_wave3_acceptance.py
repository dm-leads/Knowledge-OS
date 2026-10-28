"""Самопроверка всех волн, приёмка на контур, известные ответы волны 3 (Т42.4–Т42.5, П61)."""
import ast
from pathlib import Path

import yaml

from content_engine.cli import main
from content_engine.core import audit_store, contours
from content_engine.core.instance import Instance
from content_engine.steps import known_answers as ka
from content_engine.steps.check import OFF, wave3_items

from .conftest import TODAY, set_profile

TESTS = Path(__file__).parent


def _test_names() -> set[str]:
    names = set()
    for path in TESTS.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names |= {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    return names


def test_every_contour_has_sign_known_answers_and_tests():
    names = _test_names()
    spec = contours.spec()
    assert set(spec) == {"А", "Б", "В", "Г", "Д", "Основание"}
    for key, c in spec.items():
        assert c["sign"] and c["known"] and c["tests"], key
        assert c["check"] in contours.CHECKS, key
        assert set(c["tests"]) <= names, f"{key}: нет тестов {set(c['tests']) - names}"


def test_contours_table_is_read_only_and_honest(project):
    instance = Instance(project, today=TODAY)
    rows = contours.table(instance)
    by = {r["contour"]: r for r in rows}
    assert by["Д"]["state"] == contours.OFF                              # пульса не было — не «да»
    assert by["Г"]["state"] == contours.OFF


def test_check_contours_command(project, capsys):
    assert main(["check", "--contours", "--profile", str(project)]) == 0
    assert "Приёмка на контур" in capsys.readouterr().out


def test_wave3_items_not_connected_with_hint(project):
    items = {i["item"]: i for i in wave3_items(Instance(project, today=TODAY))}
    for name in ("сводка пульса", "деньги за окно ставки", "технический чек-лист", "пульс"):
        assert items[name]["light"] == OFF and items[name]["hint"], name


def test_known_answers_wave3_kinds(project, tmp_path, capsys):
    instance = Instance(project, today=TODAY)
    audit_store.write(instance, "full", 1, [{"id": "title_empty|example.ru/blog/a", "url": "example.ru/blog/a",
                                             "check": "title_empty", "level": "critical", "detail": "",
                                             "first_seen": TODAY.isoformat()}])
    cases = {"cases": [
        {"name": "тишина", "kind": "pulse", "facts": {"site_ok": True, "last_publication": "2026-01-01",
                                                      "stock_days": 10, "audit_age": 3}, "expect": {"color": "красный"}},
        {"name": "proxy", "kind": "bet", "record": {"bet_missing": False},
         "measured": {"step": 3, "money": {"value": 10, "status": "proxy", "note": "ступень 3"}},
         "expect": "мало данных"},
        {"name": "пустой title", "kind": "audit", "url": "https://example.ru/blog/a", "check": "title_empty",
         "level": "critical"},
        {"name": "сводка без права", "kind": "refuse", "action": "notify_without_right"},
        {"name": "расписание без права", "kind": "refuse", "action": "schedule_without_right"},
        {"name": "предложение без принятия", "kind": "refuse", "action": "propose_without_accept"}]}
    path = tmp_path / "ka3.yaml"
    path.write_text(yaml.safe_dump(cases, allow_unicode=True), encoding="utf-8")
    for case in ka.load_cases(instance, str(path)):
        good, detail = ka.run_case(instance, case, True)
        assert good, (case["name"], detail)


def test_status_shows_pulse_and_stays_read_only(project, capsys):
    import hashlib
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in project.parent.rglob("*") if p.is_file()}
    assert main(["status", "--profile", str(project)]) == 0
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in project.parent.rglob("*") if p.is_file()}
    assert before == after
    out = capsys.readouterr().out
    assert "Пульс: ещё не было" in out and "Аудит: ещё не было" in out


def test_ignored_checklist_warned(tmp_path):
    """Т42.4: технический чек-лист — решение проекта; под правилом игнорирования — предупреждение."""
    import shutil
    from content_engine.steps.check import YELLOW, _ignored
    from .conftest import base_profile
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".gitignore").write_text("*_checklist.yaml\n", encoding="utf-8")
    inst = root / "instance"
    inst.mkdir()
    (inst / "profile.yaml").write_text(yaml.safe_dump(base_profile(), allow_unicode=True), encoding="utf-8")
    (inst / "tech_checklist.yaml").write_text("schema_version: 1\nchecks: {}\n", encoding="utf-8")
    item = _ignored(Instance(inst / "profile.yaml", today=TODAY))
    assert item["light"] == YELLOW and "tech_checklist.yaml" in item["detail"]
