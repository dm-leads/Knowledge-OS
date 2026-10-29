"""Заморозка по динамике спроса (А3, Т41.1): `content-engine freeze scan | accept`.

История спроса — `data/demand_history.jsonl`: строка — фраза, месяц, частота, источник, день съёма. Пополняется из рядов,
которые уже снял сбор спроса (облако Wordstat хранит ряд у головных фраз направлений), и с `--fetch` — дозапросом ряда
`wordstat/dynamics` облака для головных запросов кластеров без ряда или с рядом старше `freeze.refetch_days`. Облако —
рубли: цена до запроса, бюджет-гейт, без `--unattended`. Месяц не дублируется: новый съём того же месяца заменяет старый.

Правило (Т41.1): нужны 24 последних полных месяца без пропусков; пропуск — «нет данных», не кандидат. Кандидат на
заморозку — сумма последних 12 месяцев ниже суммы предыдущих 12 на `drop_share` и не меньше `min_months_down` из 12
месяцев ниже того же месяца год назад (устойчиво, а не сезонная яма); средняя за последние 12 месяцев ниже
`min_frequency` — «мало спроса», не кандидат. Заморозка — решение человека: `accept` пишет `data/frozen.json`, карта
спроса (`map build`) даёт кластеру статус `frozen`.
"""
from __future__ import annotations

import json
from datetime import date

from ..core import budget, locks, store
from ..core.errors import GateRefused
from ..core.number import NO_DATA

CANDIDATE, OK, LOW, NO_SERIES = "кандидат", "спрос держится", "мало спроса", NO_DATA


def history_path(instance):
    return instance.root / "data" / "demand_history.jsonl"


def months_back(today: date, n: int = 24) -> list[str]:
    """Последние n полных месяцев по порядку, «ГГГГ-ММ»."""
    y, m = today.year, today.month - 1
    out = []
    for _ in range(n):
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
    return out[::-1]


def merge(instance, rows: list[dict]) -> int:
    """Пополнить историю: (фраза, месяц) — одна строка, новый съём заменяет старый. Вернуть число новых или заменённых."""
    with locks.held(instance, "demand_history", "write"):
        return _merge(instance, rows)


def _merge(instance, rows: list[dict]) -> int:
    path = history_path(instance)
    have = {(r["phrase"], r["month"]): r for r in store.read_jsonl(path, missing_ok=True)}
    changed = 0
    for r in rows:
        key = (r["phrase"], r["month"])
        old = have.get(key)
        if old is None or (old["count"], old["as_of"]) != (r["count"], r["as_of"]):
            have[key] = r
            changed += 1
    if changed:
        store.write_jsonl(path, sorted(have.values(), key=lambda r: (r["phrase"], r["month"])), source_count=len(have))
    return changed


def _from_demand(instance) -> list[dict]:
    out = []
    for r in store.read_jsonl(instance.file("demand"), missing_ok=True):
        h = r.get("history")
        if isinstance(h, list):
            as_of = ((r.get("numbers") or {}).get("frequency") or {}).get("as_of") or instance.today.isoformat()
            for x in h:
                out.append({"phrase": r["query"].strip().lower(), "month": x["month"][:7], "count": float(x["count"]),
                            "source": r.get("source") or "wordstat", "as_of": as_of})
    return out


def _series(instance) -> dict[str, dict]:
    by_phrase: dict[str, dict] = {}
    for r in store.read_jsonl(history_path(instance), missing_ok=True):
        s = by_phrase.setdefault(r["phrase"], {"months": {}, "as_of": ""})
        s["months"][r["month"]] = r["count"]
        s["as_of"] = max(s["as_of"], r["as_of"])
    return by_phrase


def judge(f: dict, months: list[str], series: dict | None) -> dict:
    if not series:
        return {"status": NO_SERIES, "why": "ряда нет — `freeze scan --fetch --confirm-spend`"}
    missing = [m for m in months if m not in series["months"]]
    if missing:
        return {"status": NO_SERIES, "why": f"ряд неполный: нет {len(missing)} мес. ({missing[0]} …)"}
    values = [series["months"][m] for m in months]
    prev, last = sum(values[:12]), sum(values[12:])
    mean = last / 12
    if mean < f["min_frequency"]:
        return {"status": LOW, "why": f"средняя за 12 мес. {mean:.0f} < {f['min_frequency']}", "mean": mean}
    drop = 1 - last / prev if prev > 0 else 0.0
    down = sum(1 for i in range(12, 24) if values[i] < values[i - 12])
    numbers = {"prev12": prev, "last12": last, "drop": round(drop, 3), "months_down": down, "mean": round(mean, 1)}
    if drop >= f["drop_share"] and down >= f["min_months_down"]:
        return {"status": CANDIDATE, "why": f"за год −{drop:.0%}, ниже прошлого года {down} из 12 мес.", **numbers}
    return {"status": OK, "why": f"за год {-drop:+.0%}, ниже прошлого года {down} из 12 мес.", **numbers}


def _fetch(instance, phrases: list[str]) -> int:
    from ..adapters.wordstat import CloudWordstat
    w = instance.p["sources"]["wordstat"]
    price = round(w["cloud_price_rub"] * len(phrases), 2)

    rows: list[dict] = []
    done = {"phrases": 0}

    def run():
        cloud = CloudWordstat(instance)
        for phrase in phrases:
            for month, count in cloud.dynamics(phrase, instance.p["freeze"]["months"]):
                rows.append({"phrase": phrase, "month": month, "count": count, "source": "wordstat:cloud",
                             "as_of": instance.today.isoformat()})
            done["phrases"] += 1
        return rows
    try:
        budget.paid_call(instance, "спрос", "freeze", price, run, confirm=getattr(instance, "confirm_spend", None),
                         unattended=getattr(instance, "unattended", False),
                         what=f"Wordstat облако: ряд по {len(phrases)} фразам",
                         source=f"sources.wordstat.cloud_price_rub × {len(phrases)}",
                         spent_if_broken=lambda: w["cloud_price_rub"] * done["phrases"])
    except Exception:
        if rows:                                       # оплаченные ряды до сбоя не теряются
            merge(instance, rows)
        raise
    return merge(instance, rows)


def scan(instance, *, fetch: bool = False) -> dict:
    from . import cluster as cluster_mod
    if not instance.file("clusters").is_file():
        raise GateRefused("кластеров нет — `content-engine cluster`")
    f = instance.p["freeze"]
    absorbed = merge(instance, _from_demand(instance))
    clusters = sorted(cluster_mod.load(instance), key=lambda c: -(c.get("weight") or 0))[:f["top"]]
    heads = {c["id"]: c["head"].strip().lower() for c in clusters}       # весомые кластеры: freeze.top
    fetched = 0
    if fetch:
        series = _series(instance)
        stale = sorted({h for h in heads.values() if h not in series or
                        (instance.today - date.fromisoformat(series[h]["as_of"])).days > f["refetch_days"]})
        if stale:
            fetched = _fetch(instance, stale)
    series = _series(instance)
    months = months_back(instance.today, f["months"])
    frozen = {r["cluster_id"] for r in store.read_json(instance.file("frozen"), "frozen", missing_ok=True)["records"]}
    rows = []
    for cid, head in heads.items():
        rows.append({"cluster_id": cid, "head": head, "frozen": cid in frozen, **judge(f, months, series.get(head))})
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in (CANDIDATE, OK, LOW, NO_SERIES)}
    line = store.write_json(instance.root / "data" / "freeze.json", "freeze", rows,
                            meta={"date": instance.today.isoformat(), "months": f"{months[0]} — {months[-1]}",
                                  "counts": counts, "absorbed": absorbed, "fetched": fetched})
    return {"rows": rows, "counts": counts, "months": (months[0], months[-1]), "absorbed": absorbed,
            "fetched": fetched, "write": line}


def accept(instance, cluster_id: str, who: str, *, reject: bool = False) -> dict:
    data = store.read_json(instance.root / "data" / "freeze.json", "freeze", missing_ok=True)
    row = next((r for r in data["records"] if r["cluster_id"] == cluster_id), None)
    if row is None:
        raise GateRefused(f"кластера {cluster_id} в последнем `freeze scan` нет")
    decision = {"date": instance.today.isoformat(), "cluster_id": cluster_id, "head": row["head"],
                "decision": "не заморожено" if reject else "заморожено", "who": who, "why": row["why"]}
    if not reject and row["status"] != CANDIDATE:
        raise GateRefused(f"кластер {cluster_id}: «{row['status']}» — заморозка только кандидата по правилу")
    with locks.held(instance, "frozen", "write"):
        log = store.append_jsonl(instance.root / "data" / "freeze_decisions.jsonl", [decision])
        if reject:
            return {"decision": decision["decision"], "write": log}
        records = store.read_json(instance.file("frozen"), "frozen", missing_ok=True)["records"]
        if not any(r["cluster_id"] == cluster_id for r in records):
            records.append({"cluster_id": cluster_id, "reason": f"динамика спроса: {row['why']}", "who": who,
                            "date": instance.today.isoformat()})
        line = store.write_json(instance.file("frozen"), "frozen", records)
    return {"decision": decision["decision"], "write": f"{line}; {log}"}


def render(out: dict) -> str:
    c = out["counts"]
    lines = [f"Динамика спроса {out['months'][0]} — {out['months'][1]}: кандидатов {c[CANDIDATE]}, держится {c[OK]}, "
             f"мало спроса {c[LOW]}, нет данных {c[NO_SERIES]}; из сбора спроса добавлено месяцев {out['absorbed']}, "
             f"дозапрошено {out['fetched']}"]
    for r in out["rows"]:
        if r["status"] == CANDIDATE:
            lines.append(f"- {r['cluster_id']} «{r['head']}»: {r['why']}" + (" (уже заморожен)" if r["frozen"] else ""))
    if c[NO_SERIES]:
        lines.append("ряда нет — `content-engine freeze scan --fetch --confirm-spend <₽>` (облако, цена до запроса)")
    lines.append("заморозка — решение человека: `content-engine freeze accept <кластер> --who <имя>`")
    lines.append(out["write"])
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("freeze", help="заморозка по динамике спроса: scan | accept")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("scan", help="кандидаты на заморозку по ряду за два года")
    add_profile(p)
    p.add_argument("--fetch", action="store_true", help="дозапрос ряда облаком Wordstat (платно)")
    from ..core.budget import add_flags
    add_flags(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("accept", help="заморозить кластер — решение человека")
    add_profile(p)
    p.add_argument("cluster")
    p.add_argument("--who", required=True)
    p.add_argument("--reject", action="store_true")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "scan":
        out = scan(instance, fetch=args.fetch)
        print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else render(out))
        return 0
    out = accept(instance, args.cluster, args.who, reject=args.reject)
    print(f"Кластер {args.cluster}: {out['decision']}; {out['write']}")
    return 0
