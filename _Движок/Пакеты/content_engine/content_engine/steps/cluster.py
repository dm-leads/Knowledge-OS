"""Кластеры по выдаче (А2, Т7): `content-engine cluster [--max-quota N] [--offline]`.

Жёсткая кластеризация от головного запроса (Т7.3): запросы по убыванию частоты; запрос входит в кластер головного,
если у них не меньше `serp_common_urls` общих адресов в топ-10; иначе открывает новый кластер. Цепочек «A похож на
B, B на C» нет: сравнение всегда с головным. Фразы без выдачи («нет в базе», не хватило квоты) кластеризуются по
смысловому ключу — взаимное сходство с ключом головного не ниже `key_overlap` — и помечаются этим способом (Т7.4).
id кластера — от ключа головного запроса: при пересборке тот же головной даёт тот же id, брони не теряются.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict

from ..adapters.serp import OK
from ..core import store
from ..core.normalize import Key
from ..core.quota import fetch_serp
from . import demand as demand_mod

BY_SERP, BY_KEY = "выдача", "смысловой ключ"


def cluster_id(key: Key) -> str:
    return "C" + hashlib.sha1(key.canonical().encode("utf-8")).hexdigest()[:8]


def serp_urls(serp_by_base: dict, phrase: str) -> set[str] | None:
    """Адреса топ-10 по всем базам; None — выдачи нет ни в одной базе."""
    urls, found = set(), False
    for results in serp_by_base.values():
        result = results.get(phrase)
        if result is not None and result.status == OK:
            found = True
            urls.update(url for url, _ in result.top)
    return urls if found else None


def build(instance, rows: list[dict], serp_by_base: dict) -> list[dict]:
    t = instance.p["thresholds"]
    rows = sorted(rows, key=lambda r: (-demand_mod.weight(r), r["query"]))
    clusters: list[dict] = []
    by_url: dict[str, set[int]] = defaultdict(set)
    by_stem: dict[str, set[int]] = defaultdict(set)
    head_urls: list[set[str] | None] = []
    head_keys: list[Key] = []
    for row in rows:
        key = Key.from_list(row["key"])
        urls = serp_urls(serp_by_base, row["query"])
        target = None
        if urls:
            common = defaultdict(int)
            for url in urls:
                for index in by_url[url]:
                    common[index] += 1
            fit = [(n, -i) for i, n in common.items() if n >= t["serp_common_urls"]]
            if fit:
                target = -max(fit)[1]
        elif key:
            # кандидаты — кластеры, с головным которых общих основ хватает для порога; перебор всех кластеров
            # с общим словом на десятках тысяч запросов слишком долог
            shared = defaultdict(int)
            for part in key.parts:
                for index in set().union(*(by_stem[v] for v in part)):
                    shared[index] += 1
            need = math.ceil(t["key_overlap"] * len(key) - 1e-9)
            for index in sorted(i for i, n in shared.items() if n >= need):
                head = head_keys[index]
                if key.similarity(head) >= t["key_overlap"] and head.similarity(key) >= t["key_overlap"]:
                    target = index
                    break
        member = {"query": row["query"], "method": BY_SERP if urls else BY_KEY,
                  "weight": demand_mod.weight(row)}
        if target is None:
            index = len(clusters)
            clusters.append({"id": cluster_id(key), "head": row["query"], "head_key": row["key"],
                             "method": member["method"], "queries": [member], "head_top": sorted(urls or []),
                             "date": instance.today.isoformat()})
            head_urls.append(urls)
            head_keys.append(key)
            for url in urls or ():
                by_url[url].add(index)
            for variant in key.variants:
                by_stem[variant].add(index)
        else:
            clusters[target]["queries"].append(member)
    for c in clusters:
        c["weight"] = sum(q["weight"] for q in c["queries"])
    return clusters


def load(instance) -> list[dict]:
    return store.read_json(instance.file("clusters"), "clusters", missing_ok=True)["records"]


def run_cluster(instance, *, max_quota=None, offline=False) -> dict:
    rows = demand_mod.load(instance)
    phrases = [r["query"] for r in sorted(rows, key=lambda r: -demand_mod.weight(r))]
    serp, plan = fetch_serp(instance, phrases, max_quota=max_quota, offline=offline)
    clusters = build(instance, rows, serp)
    line = store.write_json(instance.file("clusters"), "clusters", clusters, source_count=len(rows))
    by_method = defaultdict(int)
    for c in clusters:
        for q in c["queries"]:
            by_method[q["method"]] += 1
    return {"queries": len(rows), "clusters": len(clusters), "by_method": dict(by_method),
            "serp": plan.line(), "write": line,
            "singletons": sum(1 for c in clusters if len(c["queries"]) == 1)}


def register(sub):
    from ..cli import add_profile
    p = sub.add_parser("cluster", help="кластеры по выдаче из кэша и квоты")
    add_profile(p)
    p.add_argument("--max-quota", type=int, help="объектов квоты на прогон, не больше")
    p.add_argument("--offline", action="store_true", help="без запросов к выдаче: только кэш и смысловой ключ")
    from ..core.budget import add_flags
    add_flags(p)                                 # выдача при отказе Keys.so — запасной платный источник
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    report = run_cluster(instance, max_quota=args.max_quota, offline=args.offline)
    text = (f"Кластеры: запросов {report['queries']}, кластеров {report['clusters']} "
            f"(из одного запроса — {report['singletons']}); способ: "
            + ", ".join(f"{k} — {v}" for k, v in report["by_method"].items())
            + f"\n{report['serp']}\n{report['write']}")
    (instance.reports_dir() / "Кластеры.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0
