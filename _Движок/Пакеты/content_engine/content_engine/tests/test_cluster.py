"""Кластеры по выдаче (Т7.3, Т7.4)."""
from content_engine.adapters.serp import NOT_IN_BASE, OK, SerpResult
from content_engine.core.instance import Instance
from content_engine.steps.cluster import BY_KEY, BY_SERP, build

from .conftest import TODAY


def row(instance, query, weight):
    return {"query": query, "key": instance.norm.key(query).to_list(),
            "frequency": {"value": weight}, "impressions": None}


def top(phrase, urls):
    return SerpResult(phrase, "msk", OK, [(u, i + 1) for i, u in enumerate(urls)])


def test_no_chains_and_key_fallback(project):
    instance = Instance(project, today=TODAY)
    a = ["u1", "u2", "u3", "u4", "u5"]
    b = ["u3", "u4", "u5", "u6", "u7"]          # с A общих 3
    c = ["u6", "u7", "u8", "x1", "x2"]          # с B общих 2, с A — 0
    serp = {"msk": {"отопление теплицы": top("отопление теплицы", a),
                    "обогрев теплицы": top("обогрев теплицы", b),
                    "печь для теплицы": top("печь для теплицы", c),
                    "отопление теплицы зимой": SerpResult("отопление теплицы зимой", "msk", NOT_IN_BASE)}}
    rows = [row(instance, "отопление теплицы", 900), row(instance, "обогрев теплицы", 500),
            row(instance, "печь для теплицы", 300), row(instance, "отопление теплицы зимой", 100)]
    clusters = build(instance, rows, serp)
    heads = {c["head"]: [q["query"] for q in c["queries"]] for c in clusters}
    assert heads["отопление теплицы"][:2] == ["отопление теплицы", "обогрев теплицы"]
    assert "печь для теплицы" in heads                      # B~C не тянет C к A
    winter = next(q for c in clusters for q in c["queries"] if q["query"] == "отопление теплицы зимой")
    assert winter["method"] == BY_KEY
    assert next(q for c in clusters for q in c["queries"] if q["query"] == "обогрев теплицы")["method"] == BY_SERP


def test_cluster_id_stable(project):
    instance = Instance(project, today=TODAY)
    rows = [row(instance, "газон весной", 10)]
    assert build(instance, rows, {"msk": {}})[0]["id"] == build(instance, rows, {"msk": {}})[0]["id"]
