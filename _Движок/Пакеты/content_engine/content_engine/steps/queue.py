"""Очередь тем и запас (Б5, Т12): `content-engine queue build | refill | status | take | bet`.

- `build [--limit N]` — верхние по приоритету кандидаты карты спроса (free) становятся темами: головной запрос, три-пять формулировок из кластера,
  направление, приоритет. Перед входом в очередь — слои 1–3 и 5–7 без новой выдачи (слой 3 — по кэшу).
- `refill [--count N]` — полный гейт темы с бронью по верхним кандидатам, пока запас не дойдёт до нормы или не
  кончится N; подсказка N — по исторической доле отсева из журнала (нет истории — `queue.expected_rejection`).
- `take <id>` — **запрет в коде** (Т11.4а): тему без отчёта полного гейта за `gate_valid_days` дней взять нельзя;
  в день взятия выдача формулировок перепроверяется (Т11.4) — без свежей выдачи взять нельзя. Выдача дня взятия
  сохраняется снимком темы `data/briefs/<id>/serp.json` (Т18.2): общий кэш перезаписывается, снимок — нет. Объекты
  квоты Keys.so, потраченные на взятие, — строкой в `spend.jsonl` из счётчика плана выдачи (Т17.3);
- `retake <id>` — тема `taken` с живой бронью и годным отчётом гейта берётся снова со свежей выдачей и новым снимком;
  иначе — полный гейт заново.
- `status` — запас = темы `gated` с активной бронью; запас в днях = запас / ритм; запас ≤ `refill_at` — код 1.
- `bet <id> --hypothesis-id <H>` — поля ставки из гипотезы Движка роста (Т26, адаптер `growth`, только чтение); для
  проекта без Движка — вручную `--hypothesis --expectation --fail-threshold`. Решение по ставке — Движок роста,
  очередь несёт зеркало. `card` — шаблон карточки гипотезы для источника `file`.
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
    from .gate_topic import COMMERCIAL, DISPUTED, FREE, OVERLAP, gate, reserve_if_free, save_report
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
        verdict = report["verdict"] + ("" if report["complete"] else " (неполный гейт)")
        if report["verdict"] == DISPUTED and report["complete"]:
            # Т27.4: статья-риск читается при пополнении запаса, а не в день выпуска — задание судье сразу
            from .judge import make_task
            try:
                verdict += f" — задание судье: {make_task(instance, topic['id'])['task']}"
            except Exception as exc:                    # задание не помешает пополнению — причина в строке
                verdict += f" — задание судье не составлено: {exc}"
        results.append((topic["id"], verdict))
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
    try:
        fresh = dedup.run_layers(ctx, cand, {3, 4})
        kind = dedup.serp_type(ctx, cand)
    finally:
        _spend_objects(instance, tid, ctx)
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
    line = save_serp(instance, tid, cand.formulations)    # снимок — до статуса: без снимка тема не «взята»
    topic.update(status=TAKEN, taken=instance.today.isoformat())
    save(instance, topics)
    return {"taken": True, "reason": f"выдача перепроверена сегодня, пересечений нет; снимок выдачи темы — {line}"}


def _spend_objects(instance, tid: str, ctx):
    """Объекты Keys.so, потраченные на перепроверку выдачи, — строкой расхода темы (счётчик плана выдачи)."""
    from ..core import spend
    from ..core.number import FACT
    # объекты Keys.so — только за ответы Keys.so; запасной источник платит рублями через бюджет-гейт (Н6)
    requested = sum(plan.requested for plan in ctx.serp_plans if plan.source != "yandex_search")
    if requested:
        spend.add(instance, tid, "queue take", "keysso_objects", requested, FACT, "счётчик плана выдачи")


def save_serp(instance, tid: str, formulations: list[str]) -> str:
    """Снимок выдачи темы дня взятия по всем формулировкам и базам — из кэша, снятого сегодня (Т18.2)."""
    from ..core.serp_cache import SerpCache
    cache = SerpCache(instance.root, instance.p["thresholds"]["serp_cache_days"], instance.today)
    records = []
    for phrase in formulations:
        for base in instance.p["search"]["serp_bases"]:
            hit = cache.get(phrase, base, fresh_only=True)
            if hit is None:
                raise EngineError(f"тема {tid}: выдачи дня взятия по «{phrase}» ({base}) в кэше нет — снимок не снят")
            records.append(hit.to_dict())
    return store.write_json(instance.brief_dir(tid) / "serp.json", "topic_serp", records,
                            meta={"id": tid, "as_of": instance.today.isoformat(), "formulations": formulations},
                            source_count=len(records))


def retake(instance, tid: str, max_quota: int | None = None) -> dict:
    """Т18.2: тема `taken` с живой бронью и годным отчётом гейта снова `gated` — и сразу `take` со свежей выдачей."""
    from . import reservations
    topics = load(instance)
    topic = _find(topics, tid)
    full = f"тема {tid} идёт через полный гейт заново: `gate topic --from-queue {tid} --reserve`"
    if topic["status"] != TAKEN:
        raise GateRefused(f"тема {tid} в статусе {topic['status']}, перевзять можно только taken")
    active = {r["id"] for r in reservations.active(instance)}
    if topic.get("reservation_id") not in active:
        raise GateRefused(f"бронь темы {tid} не активна — {full}")
    valid = instance.p["thresholds"]["gate_valid_days"]
    report = _gate_report((topic.get("gate") or {}).get("report", ""))
    if not report or report.get("date", "") < (instance.today - timedelta(days=valid)).isoformat():
        raise GateRefused(f"отчёт гейта темы {tid} старше {valid} дней — {full}")
    topic["status"] = GATED
    save(instance, topics)
    try:
        return take(instance, tid, max_quota)
    except GateRefused:
        current = load(instance)
        back = _find(current, tid)
        if back["status"] == GATED:
            back["status"] = TAKEN           # свежей выдачи нет — тема остаётся взятой со старым снимком
            save(instance, current)
        raise


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


def set_bet(instance, tid: str, hypothesis_id: str | None, hypothesis: str | None, expectation: str | None,
            fail_threshold: str | None) -> dict:
    """Т26: ставка из гипотезы (`--hypothesis-id`) или вручную — всеми тремя полями; новые поля только добавляются."""
    from ..adapters import growth
    manual = (hypothesis, expectation, fail_threshold)
    if hypothesis_id and any(manual):
        raise EngineError("ставка — либо --hypothesis-id, либо три поля вручную, не вместе")
    topics = load(instance)
    topic = _find(topics, tid)
    bet = topic.setdefault("bet", {})
    notes = []
    if hypothesis_id:
        src = growth.source(instance)
        if src is None:
            raise EngineError("sources.growth.kind: none — источника гипотез нет; ставка вручную: --hypothesis "
                              "--expectation --fail-threshold")
        got = src.bet(hypothesis_id)
        notes = got.pop("notes")
        bet.update({k: v for k, v in got.items() if k != "windows"}, windows=got["windows"])
        source = f"гипотеза {hypothesis_id}, источник {src.kind}"
    else:
        if not all(manual):
            raise EngineError("ставка вручную — все три поля: --hypothesis --expectation --fail-threshold "
                              "(или --hypothesis-id)")
        bet.update(hypothesis=hypothesis, expectation=expectation, fail_threshold=fail_threshold)
        source = "вручную"
    return {"write": save(instance, topics), "source": source, "notes": notes, "bet": bet}


def run_card(args) -> int:
    from ..adapters.growth import card_template
    print(card_template())
    return 0


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("queue", help="очередь тем: build | refill | status | take | retake | bet")
    actions = parser.add_subparsers(dest="action", required=True)
    for name, text in (("build", "кандидаты карты спроса → темы очереди"), ("refill", "полный гейт до нормы запаса"),
                       ("status", "запас в днях"), ("take", "взять тему в работу"),
                       ("retake", "взять заново со свежей выдачей"), ("bet", "поля ставки темы"),
                       ("card", "шаблон карточки гипотезы (источник ставки file)")):
        p = actions.add_parser(name, help=text)
        if name == "card":
            p.set_defaults(func=run_card)
            continue
        add_profile(p)
        if name in ("take", "retake", "bet"):
            p.add_argument("id")
        if name == "refill":
            p.add_argument("--count", type=int, help="тем через полный гейт, не больше")
        if name == "build":
            p.add_argument("--limit", type=int, default=BUILD_LIMIT, help="верхних кандидатов за прогон")
        if name in ("refill", "take", "retake"):
            p.add_argument("--max-quota", type=int, help="объектов квоты на прогон, не больше")
        if name == "bet":
            p.add_argument("--hypothesis-id", help="номер гипотезы Движка роста: поля ставки — из её карточки")
            p.add_argument("--hypothesis", help="вручную, без Движка: гипотеза словами")
            p.add_argument("--expectation", help="вручную: ожидание в заявках и деньгах")
            p.add_argument("--fail-threshold", help="вручную: порог провала")
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
    elif args.action in ("take", "retake"):
        out = (take if args.action == "take" else retake)(instance, args.id, args.max_quota)
        text = f"Тема {args.id}: " + ("взята в работу. " if out["taken"] else "снята. ") + out["reason"]
        code = 0 if out["taken"] else 1
    else:
        out = set_bet(instance, args.id, args.hypothesis_id, args.hypothesis, args.expectation, args.fail_threshold)
        text = f"Ставка темы {args.id} записана ({out['source']}); {out['write']}" + "".join(
            f"\n  ! {n}" for n in out["notes"])
    if args.action in ("build", "refill", "status"):
        (instance.reports_dir() / f"Очередь — {args.action}.md").write_text(text + "\n", encoding="utf-8",
                                                                              newline="\n")
    print(json.dumps(out, ensure_ascii=False, indent=1) if args.json else text)
    return code
