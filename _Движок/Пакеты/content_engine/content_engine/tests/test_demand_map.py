"""Карта спроса и брони (Т8, Т9, Т11.5) на синтетическом сайте из шести страниц."""
from datetime import timedelta

from content_engine.core import store
from content_engine.core.instance import Instance
from content_engine.steps import queue as queue_mod
from content_engine.steps import reservations
from content_engine.steps.demand_map import build_map, load

from .conftest import TODAY, set_profile


def demand(instance, query, weight, pages):
    return {"query": query, "key": instance.norm.key(query).to_list(), "key_text": "", "sources": ["keysso"],
            "pages": [{"url": u, "source": "keysso", "position": pos, "impressions": imp, "frequency": weight,
                       "bid": None} for u, pos, imp in pages],
            "competitors": [], "impressions": None,
            "frequency": {"value": weight, "period_from": "2026-01-01", "period_to": "2026-01-31",
                          "source": "keysso:wsk", "status": "оценка", "as_of": "2026-03-01", "note": ""}}


def cluster(instance, cid, head, queries, head_top=()):
    return {"id": cid, "head": head, "head_key": instance.norm.key(head).to_list(), "method": "выдача",
            "queries": [{"query": q, "method": "выдача", "weight": 10} for q in queries],
            "head_top": list(head_top), "date": TODAY.isoformat(), "weight": 10 * len(queries)}


def setup(project):
    set_profile(project, lambda d: d["site"].update(sales_urls=["https://example.ru/catalog/teplicy",
                                                                "https://example.ru/catalog/obogrevateli"]))
    instance = Instance(project, today=TODAY)
    rows = [
        demand(instance, "отопление теплицы", 900, [("example.ru/blog/otoplenie-teplicy", 2, 50),
                                                   ("example.ru/blog/gazon-vesnoj", 8, 5)]),
        demand(instance, "обогреватель для теплицы", 300, [("example.ru/catalog/obogrevateli", 3, 20),
                                                          ("example.ru/blog/otoplenie-teplicy", 3, 10)]),
        demand(instance, "укрытие роз", 300, [("example.ru/blog/ukrytie-roz", 1, 40)]),
        demand(instance, "обрезка малины", 200, []),
    ]
    store.write_jsonl(instance.file("demand"), rows)
    store.write_json(instance.file("clusters"), "clusters", [
        cluster(instance, "C1", "отопление теплицы", ["отопление теплицы"]),
        cluster(instance, "C2", "обогреватель для теплицы", ["обогреватель для теплицы"]),
        cluster(instance, "C3", "укрытие роз", ["укрытие роз"]),
        cluster(instance, "C4", "обрезка малины", ["обрезка малины"]),
    ])
    return instance


def test_owner_live_overlap_statuses(project):
    instance = setup(project)
    report = build_map(instance, offline=True)
    data = load(instance)
    clusters = {c["id"]: c for c in data["clusters"]}
    assert report["pages"] == {"article": 4, "sales": 2, "other": 0}
    assert clusters["C1"]["owner"] == "otoplenie-teplicy" and clusters["C1"]["status"] == "covered"
    assert clusters["C1"]["live_overlap"]                                  # две наши страницы в топ-10
    assert clusters["C2"]["owner"] == "example.ru/catalog/obogrevateli"   # равная позиция — больше показов
    assert clusters["C2"]["status"] == "commercial"
    assert clusters["C4"]["status"] == "free"
    assert [c["cluster_id"] for c in data["candidates"]] == ["C4"]
    page = next(p for p in data["records"] if p["id"] == "otoplenie-teplicy")
    assert page["target_keys"] == ["отопление теплицы"]
    assert page["head_queries"] == ["отопление теплицы", "обогреватель для теплицы"]
    assert page["key"] and page["value"]["step"] == 4
    assert "записано 6 — прочитано 6" in report["write"]


def test_reserved_cluster_and_expiry_returns_topic(project):
    instance = setup(project)
    record = reservations.create(instance, topic="обрезка малины", key=[], cluster_id="C4", who="тест",
                                 queue_id="T0001")
    queue_mod.save(instance, [{"id": "T0001", "topic": "обрезка малины", "status": queue_mod.GATED,
                               "reservation_id": record["id"], "gate": {"date": TODAY.isoformat()}}])
    build_map(instance, offline=True)
    assert {c["id"]: c["status"] for c in load(instance)["clusters"]}["C4"] == "reserved"
    later = Instance(project, today=TODAY + timedelta(days=31))
    assert reservations.expire(later) == 1
    assert reservations.load(later)[0]["status"] == "expired"
    topic = queue_mod.load(later)[0]
    assert topic["status"] == queue_mod.CANDIDATE and topic["gate"] is None
