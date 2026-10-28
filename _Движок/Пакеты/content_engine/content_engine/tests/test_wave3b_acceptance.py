"""Самопроверка и приёмка на контур части 2 волны 3 (З80, Ч4): известные ответы новых видов, отказ «пустое
обновление», пункты `check`, модули карты — построено в коде и подключено у проекта раздельно."""
from content_engine.cli import main
from content_engine.core import contours, store
from content_engine.core.instance import Instance
from content_engine.steps import check, freeze, known_answers

from .conftest import TODAY, set_profile
from .test_refresh import pos, write_positions

URL = "https://example.ru/blog/otoplenie-teplicy"


def run(instance, case):
    return known_answers.run_case(instance, case, offline=True)


def test_known_refresh_freeze_ai_score(project):
    i = Instance(project, today=TODAY)
    write_positions(i, [pos("отопление теплицы", 6.0, 80)])
    assert run(i, {"kind": "refresh", "url": URL, "expect": "кандидат"})[0]
    assert run(i, {"kind": "refresh", "url": "https://example.ru/blog/ukrytie-roz", "expect": "нет"})[0]
    ok, why = run(i, {"kind": "refresh", "url": URL, "expect": "нет"})
    assert not ok and "кандидат" in why
    assert run(i, {"kind": "freeze", "series": [100] * 12 + [40] * 12, "expect": freeze.CANDIDATE})[0]
    assert run(i, {"kind": "freeze", "series": [100] * 24, "expect": freeze.OK})[0]
    text = ("Теплицу зимой топят печью или тёплым полом и держат 18 градусов днём и 12 ночью без перепадов. " * 3
            + "\n\n## Чем топить\n\nПечь.\n\n## Сколько стоит\n\nДорого.\n")
    ok, why = run(i, {"kind": "ai_score", "text": text, "expect": 3})
    assert ok, why


def test_empty_refresh_is_refusal(project):
    ok, why = known_answers.refuse_case(Instance(project, today=TODAY), {"action": "empty_refresh"})
    assert ok and "пустое обновление" in why


def test_check_lists_part2_items(project):
    items = {x["item"]: x for x in check.wave3b_items(Instance(project, today=TODAY))}
    assert set(items) >= {"цитирование в ответах ИИ", "поведение на странице", "история спроса для заморозки",
                          "разведка конкурентов", "поле шире освоенного"}
    assert items["поведение на странице"]["light"] == check.OFF
    set_profile(project, lambda d: d["ai_visibility"].update(citations="keysso"))
    items = {x["item"]: x for x in check.wave3b_items(Instance(project, today=TODAY))}
    assert items["цитирование в ответах ИИ"]["light"] == check.YELLOW


def test_map_modules_built_and_connection_separate(project, capsys):
    i = Instance(project, today=TODAY)
    mods = contours.modules(i)
    assert mods["total"] == 26 and len(mods["built"]) == 26 and not mods["missing"]
    assert {"Д5", "Д6", "А3"} <= set(mods["not_connected"])
    store.append_jsonl(i.root / "data" / "demand_history.jsonl",
                       [{"phrase": "x", "month": "2026-01", "count": 1, "source": "t", "as_of": "2026-02-01"}])
    assert "А3" not in contours.modules(i)["not_connected"]
    assert main(["check", "--contours", "--profile", str(project)]) == 0
    out = capsys.readouterr().out
    assert "в коде 26 из 26" in out and "не подключено у проекта — Д6" in out


def test_contours_name_new_known_kinds_and_tests():
    spec = contours.spec()
    assert {"freeze"} <= set(spec["А"]["known"]) and {"refresh", "ai_score"} <= set(spec["Д"]["known"])
    assert "test_empty_refresh_refused_by_gate" in spec["Д"]["tests"]
