"""Очередь тем и запас (Б5, Т12): `content-engine queue build | refill | status | take | bet`.

- `build [--limit N]` — верхние по приоритету кандидаты карты спроса (free) становятся темами: головной запрос, три-пять формулировок из кластера,
  направление, приоритет. Перед входом в очередь — слои 1–3 и 5–7 без новой выдачи (слой 3 — по кэшу).
- `refill [--count N]` — полный гейт темы с бронью по верхним кандидатам, пока запас не дойдёт до нормы или не
  кончится N; подсказка N — по исторической доле отсева из журнала (нет истории — `queue.expected_rejection`).
- `take <id>` — **запрет в коде** (Т11.4а): тему без отчёта полного гейта за `gate_valid_days` дней взять нельзя;
  в день взятия выдача формулировок перепроверяется (Т11.4) — без свежей выдачи взять нельзя.
- `status` — запас = темы `gated` с активной бронью; запас в днях = запас / ритм; запас ≤ `refill_at` — код 1.
- `bet <id>` — поля ставки (гипотеза, ожидание, порог провала); решение по ним — Движок роста, очередь их несёт.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from ..core import store
from ..core.errors import EngineError, GateRefused
from ..core.normalize import Key

CANDIDATE, GATED, TAKEN, PUBLISHED, REJECTED = "candidate", "gated", "taken", "published", "rejected"
PREFILTER_LAYERS = {1, 2, 3, 5, 6, 7}
WINDOWS = [90, 180]


def load(instance) -> list[dict]:
    return store.read_json(instance.file("queue"), "queue", missing_ok=True)["records"]


def save(instance, topics: list[dict]) -> str:
    return store.write_json(instance.file("queue"), "queue", topics)


def _find(topics, tid):
    for t in topics:
        if t["id"] == tid:
            return t
    raise EngineError(f"темы {tid} в очереди нет")


def candidate_of(instance, tid: str):
    from .gate_topic import Candidate
    topic = _find(load(instance), tid)
    return Candidate(topic["topic"], list(topic["formulations"]), cluster_id=topic.get("cluster_id"),
                     queue_id=topic["id"], bet=dict(topic.get("bet") or {}))


def record_gate(instance, tid: str, report: dict, path: str):
    topics = load(instance)
    topic = _find(topics, tid)
    topic["gate"] = {"date": report["date"], "verdict": report["verdict"], "complete": report["complete"],
                     "report": path}
    if report.get("reservation"):
        topic.update(status=GATED, reservation_id=report["reservation"])
    elif report["complete"] and report["verdict"] in ("пересекается", "коммерческая") \
            and topic["status"] in (CANDIDATE, GATED):
        # повторный полный гейт нашёл пересечение — тема снимается, её бронь освобождается
        if topic.get("reservation_id"):
            from . import reservations
            records = reservations.load(instance)
            for r in records:
                if r["id"] == topic["reservation_id"] and r["status"] == reservations.ACTIVE:
                    r["status"] = reservations.RELEASED
            reservations.save(instance, records)
        stop = report["stopped_by"] or {}
        topic.update(status=REJECTED, reservation_id=None,
                     rejected={"layer": stop.get("layer", 0), "reason": stop.get("reason") or
                               report["serp_type"]["reason"], "page": stop.get("page", ""),
                               "section": stop.get("section", "")})
    elif topic["status"] == GATED and not report.get("reservation"):
        topic["gate"] = topic["gate"] | {"note": "повторный гейт без брони"}
    save(instance, topics)


def priority_key(instance, topic: dict) -> tuple:
    """Т12.2: вес направления → коммерческий интент и лид-сигналы → ручной приоритет → частота."""
    weights = {d["name"]: d["weight"] for d in instance.p["directions"]}
    signal_words = instance.p["queue"]["commercial_words"] + instance.p["queue"]["lead_words"]
    text_key = instance.norm.key(" ".join(topic["formulations"]))
    signal = any((wk := instance.norm.key(w)) and wk.similarity(text_key) == 1.0 for w in signal_words)
    return (-weights.get(topic["direction"], 0), -int(signal), -(topic.get("manual_priority") or 0),
            -topic["weight"], topic["id"])


def _formulations(instance, cand: dict, demand_rows: list[dict]) -> list[str]:
    limit = instance.p["queue"]["max_formulations"]
    out = list(dict.fromkeys(cand["queries"]))[:limit]
    if len(out) < 3:
        # мало запросов в кластере — добавляем запросы спроса, где есть все основы головного запроса
        head = instance.norm.key(cand["head"])
        from .demand import weight
        extra = sorted((r for r in demand_rows if r["query"] not in out
                        and head.similarity(Key.from_list(r["key"])) == 1.0), key=lambda r: -weight(r))
        out += [r["query"] for r in extra[:limit - len(out)]]
    return out


BUILD_LIMIT = 200


def build(instance, limit: int = BUILD_LIMIT) -> dict:
    from . import demand as demand_mod
    from . import demand_map
    from . import dedup
    from .gate_topic import Candidate
    data = demand_map.load(instance)
    rows = demand_mod.load(instance)
    topics = load(instance)
    known = {t.get("cluster_id") for t in topics}
    ctx = dedup.Context(instance, cache_only=True)
    ctx.queue_topics = topics
    added = rejected = 0
    # кандидатов тысячи, а ритм — статьи в неделю: в очередь идут верхние по приоритету (Т12.2), остальные ждут
    fresh = [c for c in data["candidates"] if c["cluster_id"] not in known]
    fresh.sort(key=lambda c: priority_key(instance, {"id": c["cluster_id"], "direction": c["direction"],
                                                    "formulations": c["queries"][:3], "weight": c["weight"]}))
    for cand in fresh[:limit]:
        tid = f"T{len(topics) + 1:04d}"
        topic = {"id": tid, "topic": cand["head"], "formulations": _formulations(instance, cand, rows),
                 "cluster_id": cand["cluster_id"], "direction": cand["direction"], "weight": cand["weight"],
                 "manual_priority": 0, "status": CANDIDATE, "gate": None, "reservation_id": None,
                 "bet": {"hypothesis": "", "expectation": "", "fail_threshold": "", "windows": WINDOWS},
                 "created": instance.today.isoformat(), "rejected": None, "prefilter": None}
        topics.append(topic)
        if len(topic["formulations"]) < 3:
            topic.update(status=REJECTED, rejected={"layer": 0, "reason": "меньше трёх формулировок"})
            _log(instance, topic, 0, "меньше трёх формулировок")
            rejected += 1
            continue
        c = Candidate(topic["topic"], topic["formulations"], cluster_id=topic["cluster_id"], queue_id=tid)
        results = dedup.run_layers(ctx, c, PREFILTER_LAYERS)
        block = next((r for r in results if r.outcome == dedup.BLOCK), None)
        topic["prefilter"] = {"date": instance.today.isoformat(),
                              "layers": {str(r.layer): r.outcome for r in results}}
        if block:
            topic.update(status=REJECTED, rejected={"layer": block.layer, "reason": block.reason,
                                                    "page": block.page, "section": block.section})
            _log(instance, topic, block.layer, block.reason, block.page, block.section)
            rejected += 1
        else:
            added += 1
    line = save(instance, topics)
    return {"candidates": len(data["candidates"]), "considered": min(limit, len(fresh)), "added": added,
            "rejected": rejected, "write": line}


def _log(instance, topic, layer, reason, page="", section=""):
    store.append_jsonl(instance.file("rejections"), [{
        "date": instance.today.isoformat(), "topic": topic["topic"], "queue_id": topic["id"], "verdict": "пересекается",
        "layer": layer, "reason": reason, "page": page, "section": section}])


def stock(instance, topics: list[dict] | None = None) -> int:
    from . import reservations
    topics = load(instance) if topics is None else topics
    active = {r["id"] for r in reservations.active(instance)}
    return sum(1 for t in topics if t["status"] == GATED and t.get("reservation_id") in active)


def rejection_rate(instance) -> tuple[float, str]:
    """Доля отсева на полном гейте по журналу; нет истории — ожидаемая из профиля."""
    rows = store.read_jsonl(instance.file("rejections"), missing_ok=True)
    gated = sum(1 for t in load(instance) if t["status"] in (GATED, TAKEN, PUBLISHED)
                and t.get("gate") and t["gate"].get("complete"))
    rejected_full = sum(1 for r in rows if r.get("full_gate"))
    if gated + rejected_full == 0:
        return instance.p["queue"]["expected_rejection"], "ожидаемая из профиля"
    total = gated + rejected_full
    return rejected_full / total, f"история журнала: {rejected_full} из {total}"


def refill_hint(instance) -> tuple[int, str]:
    norm = instance.p["queue"]["norm"]
    need = max(0, norm - stock(instance))
    rate, source = rejection_rate(instance)
    count = math.ceil(need / max(0.05, 1 - rate)) if need else 0
    return count, f"нужно ещё {need} до нормы {norm}; доля отсева {rate:.2f} ({source}) → гнать {count}"


def _quarter_used(instance, topics, direction) -> int:
    q_start = date(instance.today.year, 3 * ((instance.today.month - 1) // 3) + 1, 1).isoformat()
    return sum(1 for t in topics if t["direction"] == direction and t["status"] in (GATED, TAKEN, PUBLISHED)
               and (t.get("gate") or {}).get("date", "") >= q_start)


def refill(instance, count: int | None = None, max_quota: int | None = None) -> dict:
    from .gate_topic import COMMERCIAL, FREE, OVERLAP, gate, reserve_if_free, save_report
    hint, hint_text = refill_hint(instance)
    count = hint if count is None else count
    topics = load(instance)
    quotas = {d["name"]: d["quarter_quota"] for d in instance.p["directions"]}
    order = sorted((t for t in topics if t["status"] == CANDIDATE), key=lambda t: priority_key(instance, t))
    done, spent, results = 0, 0, []
    for topic in order:
        if done >= count or stock(instance) >= instance.p["queue"]["norm"]:
            break
        quota = quotas.get(topic["direction"], 0)
        if quota and _quarter_used(instance, load(instance), topic["direction"]) >= quota:
            results.append((topic["id"], "пропущена: квота направления на квартал выбрана"))
            continue
        cand = candidate_of(instance, topic["id"])
        left = None if max_quota is None else max(0, max_quota - spent)   # предел — на весь прогон, не на тему
        report = gate(instance, cand, max_quota=left)
        spent += report["serp_requested"]
        done += 1
        if report["verdict"] == FREE and report["complete"]:
            report["reservation"] = reserve_if_free(instance, cand, report, "пополнение очереди")
        path = save_report(instance, report)
        record_gate(instance, topic["id"], report, path)
        current = load(instance)
        t = _find(current, topic["id"])
        if report["verdict"] in (OVERLAP, COMMERCIAL) and report["complete"]:
            stop = report["stopped_by"] or {}
            t.update(status=REJECTED, rejected={"layer": stop.get("layer", 0),
                                                "reason": stop.get("reason") or report["serp_type"]["reason"],
                                                "page": stop.get("page", ""), "section": stop.get("section", "")})
            save(instance, current)
            row = {"date": report["date"], "topic": report["topic"], "queue_id": topic["id"],
                   "verdict": report["verdict"], "layer": stop.get("layer", 0),
                   "reason": stop.get("reason") or report["serp_type"]["reason"], "page": stop.get("page", ""),
                   "section": stop.get("section", ""), "full_gate": True}
            store.append_jsonl(instance.file("rejections"), [row])
        results.append((topic["id"], report["verdict"] + ("" if report["complete"] else " (неполный гейт)")))
        if not report["complete"]:
            break                                     # выдачи не хватило — дальше гнать бессмысленно
    return {"hint": hint_text, "gated_now": done, "results": results, "stock": stock(instance)}


def take(instance, tid: str, max_quota: int | None = None) -> dict:
    """Взять тему в работу. Запрет в коде: без отчёта полного гейта за `gate_valid_days` — отказ."""
    from . import dedup
    from .gate_topic import FREE
    topics = load(instance)
    topic = _find(topics, tid)
    gate_info = topic.get("gate") or {}
    valid = instance.p["thresholds"]["gate_valid_days"]
    oldest = (instance.today - timedelta(days=valid)).isoformat()
    refusal = (f"тема {tid} без отчёта полного гейта «свободно» за последние {valid} дней — взять нельзя; "
               f"сначала `content-engine gate topic --from-queue {tid} --reserve`")
    report = _gate_report(gate_info.get("report", ""))
    # сверяется сам отчёт гейта, а не только поля очереди: их можно поправить руками
    if not report or report.get("queue_id") != tid or report.get("topic") != topic["topic"] \
            or not report.get("complete") or report.get("verdict") != FREE or report.get("date", "") < oldest:
        raise GateRefused(refusal)
    if topic["status"] != GATED:
        raise GateRefused(f"тема {tid} в статусе {topic['status']}, взять можно только gated")
    from . import reservations
    active = {r["id"] for r in reservations.active(instance)}
    if topic.get("reservation_id") not in active:
        raise GateRefused(f"тема {tid}: бронь кластера не активна — тему нужно снова провести через гейт")
    ctx = dedup.Context(instance, fresh=True, max_quota=max_quota)
    cand = candidate_of(instance, tid)
    fresh = dedup.run_layers(ctx, cand, {3, 4})
    kind = dedup.serp_type(ctx, cand)
    if any(r.outcome in (dedup.SKIPPED, dedup.NO_DATA) for r in fresh) or kind["outcome"] == dedup.SKIPPED:
        raise GateRefused(f"тема {tid}: свежей выдачи на сегодня нет (квота или --offline) — взять нельзя")
    block = next((r for r in fresh if r.outcome == dedup.BLOCK), None)
    if block is None and kind["outcome"] == "коммерческая":
        block = dedup.LayerResult(0, dedup.BLOCK, kind["reason"])
    if block:
        topic.update(status=REJECTED, rejected={"layer": block.layer, "reason": block.reason, "page": block.page,
                                                "section": block.section})
        save(instance, topics)
        _log(instance, topic, block.layer, f"перепроверка выдачи в день взятия: {block.reason}", block.page)
        return {"taken": False, "reason": block.reason}
    topic.update(status=TAKEN, taken=instance.today.isoformat())
    save(instance, topics)
    return {"taken": True, "reason": "выдача перепроверена сегодня, пересечений нет"}


def _gate_report(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None


def status(instance) -> tuple[dict, int]:
    topics = load(instance)
    q = instance.p["queue"]
    if not topics:
        raise EngineError("очередь пуста: сбор спроса не дал кандидатов — смотрите отчёт `demand collect` "
                          "по источникам и `map build` по статусам кластеров")
    s = stock(instance, topics)
    by_status = {k: sum(1 for t in topics if t["status"] == k)
                 for k in (CANDIDATE, GATED, TAKEN, PUBLISHED, REJECTED)}
    days = s / q["per_day"] if q["per_day"] else 0
    report = {"date": instance.today.isoformat(), "stock": s, "days": round(days, 1), "norm": q["norm"],
              "refill_at": q["refill_at"], "by_status": by_status, "below_norm": s < q["norm"],
              "hint": refill_hint(instance)[1]}
    return report, (1 if s <= q["refill_at"] else 0)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("queue", help="очередь тем: build | refill | status | take | bet")
    actions = parser.add_subparsers(dest="action", required=True)
    for name, text in (("build", "кандидаты карты спроса → темы очереди"), ("refill", "полный гейт до нормы запаса"),
                       ("status", "запас в днях"), ("take", "взять тему в работу"), ("bet", "поля ставки темы")):
        p = actions.add_parser(name, help=text)
        add_profile(p)
        if name in ("take", "bet"):
            p.add_argument("id")
        if name == "refill":
            p.add_argument("--count", type=int, help="тем через полный гейт, не больше")
        if name == "build":
            p.add_argument("--limit", type=int, default=BUILD_LIMIT, help="верхних кандидатов за прогон")
        if name in ("refill", "take"):
            p.add_argument("--max-quota", type=int, help="объектов квоты на прогон, не больше")
        if name == "bet":
            p.add_argument("--hypothesis", required=True)
            p.add_argument("--expectation", required=True)
            p.add_argument("--fail-threshold", required=True)
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    code = 0
    if args.action == "build":
        out = build(instance, args.limit)
        text = (f"Очередь: кандидатов {out['candidates']}, рассмотрено верхних {out['considered']}, "
                f"добавлено {out['added']}, снято предфильтром "
                f"{out['rejected']}; {out['write']}")
    elif args.action == "refill":
        out = refill(instance, args.count, args.max_quota)
        text = "\n".join([f"Пополнение: {out['hint']}"] + [f"  {tid}: {v}" for tid, v in out["results"]]
                         + [f"запас после пополнения: {out['stock']}"])
    elif args.action == "status":
        out, code = status(instance)
        text = (f"Запас: {out['stock']} тем ≈ {out['days']} дней (норма {out['norm']}, пополнять при ≤ "
                f"{out['refill_at']})" + (" — НИЖЕ НОРМЫ" if out["below_norm"] else "")
                + "\nПо статусам: " + ", ".join(f"{k} — {v}" for k, v in out["by_status"].items())
                + f"\n{out['hint']}")
    elif args.action == "take":
        out = take(instance, args.id, args.max_quota)
        text = f"Тема {args.id}: " + ("взята в работу. " if out["taken"] else "снята. ") + out["reason"]
        code = 0 if out["taken"] else 1
    else:
        topics = load(instance)
        topic = _find(topics, args.id)
        topic["bet"].update(hypothesis=args.hypothesis, expectation=args.expectation,
                            fail_threshold=args.fail_threshold)
        out = {"write": save(instance, topics)}
        text = f"Ставка темы {args.id} записана; {out['write']}"
    if args.action in ("build", "refill", "status"):
        (instance.reports_dir() / f"Очередь — {args.action}.md").write_text(text + "\n", encoding="utf-8",
                                                                              newline="\n")
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else text)
    return code
