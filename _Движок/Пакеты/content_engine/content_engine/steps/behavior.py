"""Поведение на странице (Д6, Т38) — опция: `content-engine behavior scan | accept | check`.

Источник — отчёты веб-аналитики (`behavior.kind`: `metrika` — API Метрики, только чтение; `none` — «не подключено»).
Окно — `behavior.window_days` до вчера. Находки (все пороги — профиль):
- обрыв: шаг воронки `funnel`, через который прошло не меньше `min_people`, где ушедших до следующего шага больше
  `drop_share`, а у соседних шагов — не больше `drop_share` / 2 («ровные соседи»);
- тупик: страница входа с посетителями не меньше `min_people`, глубина не больше 1,2 и ноль целей;
- устройство: отказ на одном устройстве в `device_ratio` раз выше среднего остальных, на каждом не меньше `min_people`;
- выпавшая из группы: страница раздела с отказом выше медианы раздела на два межквартильных размаха (в разделе не
  меньше пяти страниц с `min_people`).
Решение — только на шаге, через который прошло не меньше `min_people` (Т38.2). `accept` — человек утверждает одну правку:
она идёт кандидатом дожима (`refresh scan`, `refresh.allow_behavior`). `check` — на дни `recheck_days` после публикации
правки сравнивает показатель находки за равное окно после с окном до дня публикации: лучше на `min_gain` — «перенести
на соседние страницы раздела», иначе — «откатить» (`publish rollback`); решает человек.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, timedelta

from ..core import publications, store
from ..core.errors import EngineError, GateRefused, source_failures
from ..core.number import ESTIMATE, FACT
from ..core.urls import norm_url, section_of

DEAD_DEPTH = 1.2
LOWER_BETTER = {"drop": True, "dead_end": False, "device": True, "outlier": True}


def _client(instance, transport=None):
    from ..adapters.metrika import Metrika
    from ..core.env_file import secret
    b = instance.p["behavior"]
    if b["kind"] == "none":
        return None
    if b["kind"] != "metrika":
        from ..core.plugins import load_class
        cls = load_class(instance, b["kind"], {}, "behavior.kind")
        return cls(instance, transport=transport or instance.transport)
    return Metrika(secret(instance.p, b["token_env"]), secret(instance.p, b["counter_env"]),
                   transport=transport if transport is not None else instance.transport)


def _window(instance, end: date | None = None) -> tuple[str, str]:
    end = end or instance.today - timedelta(days=1)
    return (end - timedelta(days=instance.p["behavior"]["window_days"] - 1)).isoformat(), end.isoformat()


def _folder(instance):
    return instance.root / "data" / "behavior"


def funnel_findings(b: dict, steps: list[tuple[str, float]]) -> list[dict]:
    out = []
    drops = []
    for i in range(len(steps) - 1):
        users, nxt = steps[i][1], steps[i + 1][1]
        drops.append(None if users <= 0 else max(0.0, 1 - nxt / users))
    for i, share in enumerate(drops):
        if share is None or steps[i][1] < b["min_people"] or share <= b["drop_share"]:
            continue
        neighbours = [drops[j] for j in (i - 1, i + 1) if 0 <= j < len(drops) and drops[j] is not None]
        if all(n <= b["drop_share"] / 2 for n in neighbours):
            out.append({"id": f"drop|{steps[i][0]}", "kind": "drop", "url": steps[i][0],
                        "what": f"с шага «{steps[i][0]}» до «{steps[i + 1][0]}» уходят {share:.0%} из "
                                f"{steps[i][1]:.0f}", "metric": share, "people": steps[i][1]})
    return out


def page_findings(b: dict, pages: list[dict], devices: list[dict]) -> list[dict]:
    out = []
    for pg in pages:
        if pg["users"] >= b["min_people"] and pg["depth"] <= DEAD_DEPTH and not pg["goals"]:
            out.append({"id": f"dead_end|{norm_url(pg['url'])}", "kind": "dead_end", "url": norm_url(pg["url"]),
                        "what": f"вход {pg['users']:.0f} человек, глубина {pg['depth']:.2f}, целей 0",
                        "metric": pg["depth"], "people": pg["users"]})
    by_url: dict[str, list[dict]] = {}
    for d in devices:
        if d["users"] >= b["min_people"]:
            by_url.setdefault(norm_url(d["url"]), []).append(d)
    for url, rows in by_url.items():
        for d in rows:
            others = [x["bounce"] for x in rows if x is not d]
            if others and d["bounce"] >= b["device_ratio"] * statistics.mean(others) and d["bounce"] > 0:
                out.append({"id": f"device|{url}|{d['device']}", "kind": "device", "url": url, "device": d["device"],
                            "what": f"отказ на «{d['device']}» {d['bounce']:.0%} против {statistics.mean(others):.0%}",
                            "metric": d["bounce"], "people": d["users"]})
    groups: dict[str, list[dict]] = {}
    for pg in pages:
        if pg["users"] >= b["min_people"] and section_of(pg["url"]):
            groups.setdefault(section_of(pg["url"]), []).append(pg)
    for section, rows in groups.items():
        if len(rows) < 5:
            continue
        q = statistics.quantiles([x["bounce"] for x in rows], n=4)
        med, iqr = q[1], q[2] - q[0]
        for pg in rows:
            if iqr > 0 and pg["bounce"] > med + 2 * iqr:
                out.append({"id": f"outlier|{norm_url(pg['url'])}", "kind": "outlier", "url": norm_url(pg["url"]),
                            "what": f"отказ {pg['bounce']:.0%} при медиане раздела «{section}» {med:.0%}",
                            "metric": pg["bounce"], "people": pg["users"]})
    return out


def scan(instance, *, transport=None) -> dict:
    client = _client(instance, transport)
    if client is None:
        return {"status": "не подключено", "why": "behavior.kind: none — подключить: metrika (токен и счётчик по именам "
                                                   "переменных behavior.token_env, counter_env)"}
    b = instance.p["behavior"]
    frm, to = _window(instance)
    try:
        pages = client.entry_pages(frm, to)
        devices = client.by_device(frm, to)
        steps = [(s, client.step_users(frm, to, s)) for s in b["funnel"]]
    except source_failures() as exc:
        return {"status": "источник не ответил", "why": str(exc)}
    findings = funnel_findings(b, steps) + page_findings(b, pages, devices)
    out = {"status": "ок", "period": f"{frm} — {to}", "findings": findings,
           "number_status": {"pages": FACT, "funnel": ESTIMATE}, "steps": steps}
    out["write"] = store.write_json(_folder(instance) / f"{instance.today.isoformat()}.json", "behavior", findings,
                                    meta={k: v for k, v in out.items() if k != "findings"})
    return out


def _last_scan(instance) -> dict:
    files = sorted(_folder(instance).glob("20*.json")) if _folder(instance).is_dir() else []
    if not files:
        raise GateRefused("находок поведения нет — `content-engine behavior scan`")
    return store.read_json(files[-1], "behavior")


def accept(instance, fid: str, who: str, *, reject: bool = False) -> dict:
    scan_data = _last_scan(instance)
    f = next((x for x in scan_data["records"] if x["id"] == fid), None)
    if f is None:
        raise GateRefused(f"находки {fid} в последнем прогоне нет")
    if f["people"] < instance.p["behavior"]["min_people"]:
        raise GateRefused(f"на шаге {f['people']:.0f} человек, меньше behavior.min_people — решение не принимается")
    row = {"date": instance.today.isoformat(), "id": fid, "kind": f["kind"], "url": f["url"], "what": f["what"],
           "device": f.get("device", ""), "before": f["metric"],
           "decision": "не утверждено" if reject else "утверждено", "who": who}
    line = store.append_jsonl(_folder(instance) / "decisions.jsonl", [row])
    return {"decision": row["decision"], "write": line}


def _metric(client, f: dict, frm: str, to: str, funnel: list[str]) -> float | None:
    if f["kind"] == "drop":
        i = funnel.index(f["url"]) if f["url"] in funnel else -1
        if i < 0 or i + 1 >= len(funnel):
            return None
        a, b = client.step_users(frm, to, funnel[i]), client.step_users(frm, to, funnel[i + 1])
        return None if a <= 0 else max(0.0, 1 - b / a)
    if f["kind"] == "device":
        rows = [d for d in client.by_device(frm, to) if norm_url(d["url"]) == f["url"] and d["device"] == f["device"]]
        return rows[0]["bounce"] if rows else None
    rows = [p for p in client.entry_pages(frm, to) if norm_url(p["url"]) == f["url"]]
    if not rows:
        return None
    return rows[0]["depth"] if f["kind"] == "dead_end" else rows[0]["bounce"]


def check(instance, *, transport=None) -> dict:
    client = _client(instance, transport)
    if client is None:
        return {"status": "не подключено", "results": []}
    b = instance.p["behavior"]
    decisions = [d for d in store.read_jsonl(_folder(instance) / "decisions.jsonl", missing_ok=True)
                 if d["decision"] == "утверждено"]
    events = publications.read(instance)
    results = []
    for d in decisions:
        pub = next((e for e in events if e.get("event") == "published" and norm_url(e.get("url", "")) == d["url"]
                    and e.get("day", "") >= d["date"]), None)
        if pub is None:
            results.append({"id": d["id"], "status": "правка не опубликована"})
            continue
        day = date.fromisoformat(pub["day"])
        for n in b["recheck_days"]:
            if (instance.today - day).days < n:
                results.append({"id": d["id"], "day": n, "status": f"рано: замер на {n}-й день — "
                                                                   f"{(day + timedelta(days=n)).isoformat()}"})
                continue
            before = _metric(client, d, *_window(instance, day - timedelta(days=1)), b["funnel"])
            after = _metric(client, d, day.isoformat(), (day + timedelta(days=n - 1)).isoformat(), b["funnel"])
            if before is None or after is None:
                results.append({"id": d["id"], "day": n, "status": "нет данных", "why": "показатель не отдан отчётом"})
                continue
            better = after <= before * (1 - b["min_gain"]) if LOWER_BETTER[d["kind"]] else \
                after >= before * (1 + b["min_gain"])
            advice = ("перенести на соседние страницы раздела" if better else
                      f"откатить: `content-engine publish rollback {pub.get('id', '<id>')} --apply` — решает человек")
            results.append({"id": d["id"], "day": n, "status": "ок", "before": before, "after": after,
                            "advice": advice})
    return {"status": "ок", "results": results}


def render(out: dict) -> str:
    if out["status"] != "ок":
        return f"Поведение: {out['status']} — {out.get('why', '')}"
    lines = [f"Поведение за {out['period']}: находок {len(out['findings'])} (страницы — факт, воронка — оценка)"]
    lines += [f"- {f['id']}: {f['what']}" for f in out["findings"]]
    lines.append("дальше: `content-engine behavior accept <id> --who <имя>` — одна правка; замер — `behavior check`")
    lines.append(out["write"])
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("behavior", help="поведение на странице (опция): scan | accept | check")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("scan", help="находки по отчётам веб-аналитики")
    add_profile(p)
    p.add_argument("--unattended", action="store_true", help="запуск по расписанию")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("accept", help="утвердить одну правку по находке")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--who", required=True)
    p.add_argument("--reject", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("check", help="замер правки на 14-й и 28-й день")
    add_profile(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "scan":
        out = scan(instance)
        print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else render(out))
        return 0 if out["status"] in ("ок", "не подключено") else 3
    if args.action == "accept":
        out = accept(instance, args.id, args.who, reject=args.reject)
        print(f"Находка {args.id}: {out['decision']}; {out['write']}")
        return 0
    out = check(instance)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("Замер правок по поведению: " + out["status"])
        for r in out["results"]:
            print(f"- {r['id']}" + (f" (день {r['day']})" if r.get("day") else "") + f": {r['status']}"
                  + (f" — {r['before']:.2f} → {r['after']:.2f}; {r['advice']}" if r.get("advice") else ""))
    return 0
