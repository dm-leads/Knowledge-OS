"""Гейт темы (Т11): семь слоёв анти-дублей, тип выдачи, поля ставки → вердикт и отчёт.

Вердикты и коды выхода: «свободно» — 0, «пересекается» и «коммерческая» — 1, «спорно» — 2, ошибка входа или
источника — 3. Отладочные флаги `--offline` (без запросов к выдаче), `--layers` (только названные слои) и
`--exclude` (статья не считается), а также слой без данных дают «неполный гейт»: по нему нельзя ни забронировать
кластер, ни взять тему в работу. Отчёт — текст и JSON рядом.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..core.errors import EngineError
from ..core.normalize import Key
from . import dedup

FREE, OVERLAP, DISPUTED, COMMERCIAL = "свободно", "пересекается", "спорно", "коммерческая"
EXIT = {FREE: 0, OVERLAP: 1, COMMERCIAL: 1, DISPUTED: 2}
BET_FIELDS = ("hypothesis", "expectation", "fail_threshold")


@dataclass
class Candidate:
    topic: str
    formulations: list[str]
    cluster_id: str | None = None
    queue_id: str | None = None
    bet: dict = field(default_factory=dict)
    skip_slugs: frozenset = frozenset()     # своя статья при проверке «тема против корпуса без неё»

    def key(self, norm) -> Key:
        return norm.key(self.topic)


def check_formulations(formulations: list[str]):
    unique = list(dict.fromkeys(f.strip() for f in formulations if f.strip()))
    if len(unique) < dedup.MIN_FORMULATIONS:
        raise EngineError(f"формулировок {len(unique)}, нужно не меньше {dedup.MIN_FORMULATIONS}: "
                          "одна удобная формулировка обманывает проверку")
    return unique


def bet_check(instance, candidate: Candidate) -> dict:
    empty = [name for name in BET_FIELDS if not str(candidate.bet.get(name) or "").strip()]
    if not empty:
        return {"outcome": dedup.PASS, "reason": "поля ставки заполнены"}
    reason = f"пустые поля ставки: {', '.join(empty)} (окна замера 90 и 180 дней)"
    if instance.p["queue"]["require_bet"]:
        return {"outcome": dedup.WARN, "reason": reason}
    return {"outcome": "замечание", "reason": reason}


def gate(instance, candidate: Candidate, *, offline: bool = False, layers: set[int] | None = None,
         fresh_serp: bool = False, max_quota: int | None = None) -> dict:
    """Прогнать гейт и вернуть отчёт (словарь). Ничего не бронирует и не пишет в очередь."""
    candidate.formulations = check_formulations(candidate.formulations)
    ctx = dedup.Context(instance, offline=offline, fresh=fresh_serp, max_quota=max_quota)
    results = dedup.run_layers(ctx, candidate, layers)
    serp_type = dedup.serp_type(ctx, candidate) if (layers is None or 0 in layers) else \
        {"outcome": dedup.SKIPPED, "reason": "слой не выбран"}
    bet = bet_check(instance, candidate) if layers is None else {"outcome": dedup.SKIPPED, "reason": "слой не выбран"}
    first_block = next((r for r in results if r.outcome == dedup.BLOCK), None)
    if first_block:
        verdict = OVERLAP
    elif serp_type["outcome"] == COMMERCIAL:
        verdict = COMMERCIAL
    elif any(r.outcome == dedup.WARN for r in results) or bet["outcome"] == dedup.WARN:
        verdict = DISPUTED
    else:
        verdict = FREE
    # полный гейт — все слои посчитаны: пропуск по квоте и «нет данных» (нет карты спроса) делают его неполным
    complete = (not offline and layers is None and not candidate.skip_slugs and serp_type["outcome"] != dedup.SKIPPED
                and all(r.outcome not in (dedup.SKIPPED, dedup.NO_DATA) for r in results))
    return {
        "date": instance.today.isoformat(), "topic": candidate.topic, "formulations": candidate.formulations,
        "queue_id": candidate.queue_id, "cluster_id": candidate.cluster_id, "verdict": verdict,
        "complete": complete, "stopped_by": first_block.to_dict() if first_block else None,
        "layers": [r.to_dict() for r in results], "serp_type": serp_type, "bet": bet,
        "flags": {"offline": offline, "layers": sorted(layers) if layers else None,
                  "exclude": sorted(candidate.skip_slugs)},
        "serp_requested": sum(plan.requested for plan in ctx.serp_plans),
    }


def render(report: dict) -> str:
    lines = [f"Гейт темы «{report['topic']}» — {report['date']}",
             f"Вердикт: {report['verdict'].upper()}" + ("" if report["complete"] else "  (НЕПОЛНЫЙ ГЕЙТ: бронь и взятие "
                                                          "темы по нему невозможны)"),
             "Формулировки: " + "; ".join(report["formulations"])]
    if report["stopped_by"]:
        s = report["stopped_by"]
        lines.append(f"Остановил слой {s['layer']} «{s['name']}»: {s['reason']}"
                     + (f" — {s['page']}" if s["page"] else "") + (f", раздел «{s['section']}»" if s["section"] else ""))
    lines.append("")
    for r in report["layers"]:
        where = (f" — {r['page']}" if r["page"] else "") + (f", раздел «{r['section']}»" if r["section"] else "")
        lines.append(f"  {r['layer']}. {r['name']}: {r['outcome']}. {r['reason']}{where}")
        if r["layer"] == 3 and r["numbers"].get("pages_with_serp"):
            lines.append(f"       выдача головных запросов есть у страниц: {r['numbers']['pages_with_serp']} "
                         "(у остальных слой 3 не проверен — она снимается по квоте)")
        if r["layer"] == 2:
            for hit in r["numbers"].get("body_top", [])[:3]:
                lines.append(f"       статья: вес {hit['weight']} — {hit['title']} ({hit['slug']}) {hit['hits']}")
            for hit in r["numbers"].get("section_top", [])[:3]:
                lines.append(f"       раздел: вес {hit['weight']} — «{hit['section']}» в {hit['slug']} "
                             f"(формулировка «{hit['formulation']}»)")
            if r["numbers"].get("section_note"):
                lines.append(f"       {r['numbers']['section_note']}")
    lines.append(f"  тип выдачи: {report['serp_type']['outcome']}. {report['serp_type']['reason']}")
    lines.append(f"  ставка: {report['bet']['outcome']}. {report['bet']['reason']}")
    return "\n".join(lines)


def _slug(text: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "-", text.lower().replace("ё", "е")).strip("-")[:60] or "тема"


def save_report(instance, report: dict) -> str:
    folder = instance.reports_dir("gates")
    stem = (report["queue_id"] + "-" if report["queue_id"] else "") + _slug(report["topic"])
    (folder / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8",
                                         newline="\n")
    (folder / f"{stem}.txt").write_text(render(report) + "\n", encoding="utf-8", newline="\n")
    return str(folder / f"{stem}.json")


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("gate", help="гейт: topic | article")
    kinds = parser.add_subparsers(dest="kind", required=True)
    p = kinds.add_parser("topic", help="гейт темы: семь слоёв анти-дублей, тип выдачи, ставка")
    add_profile(p)
    p.add_argument("topic", nargs="?", help="тема")
    p.add_argument("-f", "--formulation", action="append", default=[], help="формулировка (не меньше трёх)")
    p.add_argument("--from-queue", help="id темы в очереди")
    p.add_argument("--reserve", action="store_true", help="при «свободно» забронировать кластер")
    p.add_argument("--who", default="", help="кто бронирует")
    p.add_argument("--offline", action="store_true", help="без запросов к выдаче (неполный гейт)")
    p.add_argument("--layers", help="только эти слои, через запятую; 0 — тип выдачи (неполный гейт)")
    p.add_argument("--exclude", action="append", default=[],
                   help="slug статьи, которую не считать (проверка; неполный гейт)")
    p.add_argument("--json", action="store_true", help="печатать отчёт JSON")
    p.set_defaults(func=run)
    from . import gate_article
    gate_article.register_kind(kinds)


def run(args) -> int:
    from ..cli import open_instance
    from . import queue as queue_mod
    instance = open_instance(args)
    layers = parse_layers(args.layers) if args.layers else None
    if args.from_queue:
        candidate = queue_mod.candidate_of(instance, args.from_queue)
    else:
        if not args.topic:
            raise EngineError("нужна тема или --from-queue")
        candidate = Candidate(args.topic, args.formulation)
    candidate.skip_slugs = frozenset(args.exclude)
    report = gate(instance, candidate, offline=args.offline, layers=layers)
    if args.reserve:
        report["reservation"] = reserve_if_free(instance, candidate, report, args.who or "гейт темы")
    path = save_report(instance, report)
    if report["verdict"] in (OVERLAP, COMMERCIAL) and report["complete"]:
        log_rejection(instance, report)
    if args.from_queue:
        queue_mod.record_gate(instance, args.from_queue, report, path)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else render(report))
    print(f"\nотчёт: {path}")
    return EXIT[report["verdict"]]


def log_rejection(instance, report: dict) -> str:
    """Журнал отсева (Т10.2): слой, причина, страница и раздел, с которыми пересеклась тема. Только добавлением."""
    from ..core import store
    stop = report["stopped_by"] or {}
    reason = stop.get("reason") or report["serp_type"]["reason"]
    row = {"date": report["date"], "topic": report["topic"], "queue_id": report["queue_id"],
           "verdict": report["verdict"], "layer": stop.get("layer", 0), "reason": reason,
           "page": stop.get("page", ""), "section": stop.get("section", "")}
    return store.append_jsonl(instance.file("rejections"), [row])


def parse_layers(text: str) -> set[int]:
    try:
        layers = {int(x) for x in text.split(",") if x.strip()}
    except ValueError as exc:
        raise EngineError(f"--layers: номера слоёв через запятую, получено «{text}»") from exc
    wrong = layers - set(range(0, 8))
    if not layers or wrong:
        raise EngineError(f"--layers: слои 1–7 и 0 (тип выдачи), неизвестные: {sorted(wrong) or 'пусто'}")
    return layers


def reserve_if_free(instance, candidate: Candidate, report: dict, who: str):
    from . import reservations
    if report["verdict"] != FREE:
        return None
    if not report["complete"]:
        raise EngineError("неполный гейт (--offline или --layers): бронь по нему невозможна")
    record = reservations.create(instance, topic=candidate.topic, key=candidate.key(instance.norm).to_list(),
                                 cluster_id=candidate.cluster_id, who=who, queue_id=candidate.queue_id)
    return record["id"]
