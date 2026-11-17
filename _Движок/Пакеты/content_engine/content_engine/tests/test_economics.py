"""Экономика статьи и закрытие направления (Т40, П59)."""
from content_engine.core import spend, store
from content_engine.core.instance import Instance
from content_engine.steps import economics

from .conftest import TODAY, set_profile


def rec(rid, direction, *, step=1, leads=0.0, money=0.0, status="факт", expected=10, bet=True):
    m = {"step": step, "money": {"value": money if status != "нет данных" else None, "status": status,
                                 "source": "file:roistat", "period_from": "2025-01-01", "period_to": "2025-06-29"},
         "leads": {"value": leads, "status": "факт"} if step <= 2 else None, "proposed": "", "result": None}
    return {"id": rid, "url": f"example.ru/blog/{rid}", "direction": direction, "published": "2025-01-01",
            "measure": {"90": "2025-04-01", "180": "2025-06-30"}, "measures": {"90": m, "180": m},
            "bet_missing": not bet, "expectation_numbers": [{"value": expected, "unit": "заявки"}] if bet else []}


def test_incomplete_cost_named(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [rec("A1", "огород", money=5000)])
    spend.add(instance, "A1", "картинки", "rub", 12, "факт", "генератор")
    spend.add(instance, "A1", "текст", "tokens", None, "нет данных", "агент по подписке")
    data = economics.report(instance)
    assert data["articles"][0]["cost"]["complete"] is False
    assert "неполная" in economics.render(data)


def test_direction_without_leads_proposed_to_close(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [rec(f"A{i}", "огород") for i in range(3)])
    assert any("закрыть направление «огород»" in p for p in economics.report(instance)["proposals"])


def test_proxy_direction_not_proposed(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [rec(f"A{i}", "огород", step=3, status="proxy", money=100)
                                                     for i in range(3)])
    assert economics.report(instance)["proposals"] == []


def test_unknown_money_and_cost_are_no_data_not_zero(project):
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [rec("A1", "огород", step=4, status="нет данных")])
    data = economics.report(instance)
    d = data["directions"][0]
    assert d["status"] == "нет данных" and d["payback"] is None
    row = [l for l in economics.render(data).splitlines() if l.startswith("| огород")][0]
    assert row.count("нет данных") >= 3 and "| 0 |" not in row


def test_close_only_by_window_180_and_above_noise(project):
    """1.6.0: окно 90 — только сигнал; ожидание ниже порога шума — рано; без ставок — закрытие данными невозможно."""
    instance = Instance(project, today=TODAY)
    store.write_json(instance.file("bets"), "bets", [rec(f"A{i}", "огород") for i in range(3)])
    set_profile(project, lambda d: d["economics"].update(close_window=90))
    got = economics.report(Instance(project, today=TODAY))["proposals"]
    assert len(got) == 1 and got[0].startswith("предварительный сигнал")
    set_profile(project, lambda d: d["economics"].update(close_window=180))
    store.write_json(instance.file("bets"), "bets", [rec(f"A{i}", "огород", expected=1) for i in range(3)])
    got = economics.report(Instance(project, today=TODAY))["proposals"]
    assert len(got) == 1 and "закрывать рано" in got[0]
    store.write_json(instance.file("bets"), "bets", [rec(f"A{i}", "огород", bet=False) for i in range(3)])
    got = economics.report(Instance(project, today=TODAY))["proposals"]
    assert len(got) == 1 and "закрытие данными невозможно" in got[0]


def test_proxy_leads_number_does_not_close(project):
    """Проверка Codex 18.11.2026: число заявок со статусом proxy — не данные, закрытие не предлагается."""
    instance = Instance(project, today=TODAY)
    rows = [rec(f"A{i}", "огород", step=2) for i in range(3)]
    for r in rows:
        for m in r["measures"].values():
            m["leads"] = {"value": 0, "status": "proxy"}
    store.write_json(instance.file("bets"), "bets", rows)
    assert not any("закрыть" in p for p in economics.report(instance)["proposals"])
