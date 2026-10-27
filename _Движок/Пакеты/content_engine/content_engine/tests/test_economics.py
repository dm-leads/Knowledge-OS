"""Экономика статьи и закрытие направления (Т40, П59)."""
from content_engine.core import spend, store
from content_engine.core.instance import Instance
from content_engine.steps import economics

from .conftest import TODAY, set_profile


def rec(rid, direction, *, step=1, leads=0.0, money=0.0, status="факт"):
    m = {"step": step, "money": {"value": money if status != "нет данных" else None, "status": status,
                                 "source": "file:roistat", "period_from": "2025-01-01", "period_to": "2025-06-29"},
         "leads": {"value": leads, "status": "факт"} if step <= 2 else None, "proposed": "", "result": None}
    return {"id": rid, "url": f"example.ru/blog/{rid}", "direction": direction, "published": "2025-01-01",
            "measure": {"90": "2025-04-01", "180": "2025-06-30"}, "measures": {"90": m, "180": m}}


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
