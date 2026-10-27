"""Судья спорного (Б3, Т27): `content-engine judge task | check | accept <id>`.

- `task <id>` — тема со «спорно» в гейте. Пакет находит ближайшие разделы корпуса (`judge.top`, по умолчанию пять):
  лексической пятёркой по весам слоя 2 без установки или, если проект включил `judge.embeddings.kind: api`, —
  перестановкой двадцати лексических кандидатов по эмбеддингам облачного провайдера через бюджет-гейт (Д1: локальной
  модели нет). Задание роли `judge` (`task_judge.md`): тема, формулировки, разделы текстом с id, статья-риск из отчёта
  гейта целиком — какие её подтемы закрыты, каких нет (Т27.4), схема ответа `judge.json`.
- `check <id> [--dry-run]` — ответ проверяет код: раздел — из пятёрки, цитата — подстрока его текста (пробелы и
  кавычки нормализуются), у «свободно» есть угол. Иначе ответ не принят (код 1). Принятый — строка в
  `data/judgements.jsonl` и итог в очереди: «свободно с углом» — бронь и тема `gated` с углом; «закрыто разделом» —
  тема снята, строка в журнале отсева; «спорно» — код 2, решает человек. `--dry-run` — только печать вердикта.
- `accept <id> --who <имя> --verdict free|closed [--angle …]` — решение человека по «спорно».
Модель судье не нужна в пакете: задание выполняет агент роли `judge` (Р11).
"""
from __future__ import annotations

import json
import re

from ..core import spend, store, tasks
from ..core.errors import EngineError, GateRefused
from ..core.number import FACT, NO_DATA
from .dedup import BODY_CAP, HEADING_BONUS, concepts_of
from .gate_topic import DISPUTED

VERDICTS = ("free_with_angle", "closed_by", "disputed")
CONTEXT, ANSWER = "judge_context.json", "judge.json"
POOL = 20
SCHEMA = """verdict: free_with_angle | closed_by | disputed   # свободно с углом / закрыто разделом / спорно — к человеку
section_id: "<slug>#<номер>"     # раздел из списка ниже, на котором основан вывод
quote: "<дословная цитата из этого раздела>"
angle: "<угол статьи: чего в найденных разделах нет>"   # обязателен при free_with_angle
tokens: 0                         # расход токенов, если известен
model_used: "<модель и версия>\""""


def _norm(text: str) -> str:
    text = re.sub(r"[«»“”„\"]", '"', text or "").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", text).strip().lower()


def lexical(instance, formulations: list[str], top: int) -> list[dict]:
    """Разделы корпуса по весам слоя 2: вхождения основ темы (с потолком) плюс бонус за основы в подзаголовке."""
    index = instance.index()
    norm = instance.norm
    concepts = concepts_of([norm.key(f) for f in formulations])
    by_slug = {p.slug: p for p in instance.corpus()[0]}
    scored = []
    for item in index.pages:
        page = by_slug.get(item.page.slug)
        for n, section in enumerate(item.sections):
            counts = [index.count(section.counts, part) for part in concepts]
            if not any(counts):
                continue
            heading = norm.tokens(section.heading or "")
            in_heading = sum(1 for p in concepts if any(t in index.matches(p) for t in heading))
            weight = sum(min(c, BODY_CAP) for c in counts) + HEADING_BONUS * in_heading
            text = page.sections[n].text if page and n < len(page.sections) else ""
            scored.append({"id": f"{item.page.slug}#{n}", "url": item.page.url, "title": item.page.title,
                           "heading": section.heading or "(вступление)", "text": text.strip(),
                           "weight": round(weight, 2), "matched": sum(1 for c in counts if c)})
    scored.sort(key=lambda s: (-s["matched"], -s["weight"], s["id"]))
    return scored[:top]


def nearest(instance, tid: str, formulations: list[str], *, confirm=None, unattended=False) -> tuple[list[dict], str]:
    top = instance.p["judge"]["top"]
    emb = instance.p["judge"]["embeddings"]
    if emb["kind"] != "api":
        return lexical(instance, formulations, top), "лексическая пятёрка"
    from ..adapters import embeddings
    pool = lexical(instance, formulations, POOL)
    query = " ".join(formulations)
    vecs = embeddings.vectors(instance, [query] + [f"{s['heading']}\n{s['text']}" for s in pool], tid=tid,
                              confirm=confirm, unattended=unattended)
    for s, v in zip(pool, vecs[1:]):
        s["similarity"] = round(embeddings.cosine(vecs[0], v), 4)
    pool.sort(key=lambda s: (-s["similarity"], s["id"]))
    return pool[:top], "эмбеддинги API"


def _risk_page(instance, topic: dict) -> dict | None:
    """Статья-риск — страница, на которой гейт сказал «спорно» (Т27.4): читается целиком при пополнении запаса."""
    report_path = (topic.get("gate") or {}).get("report")
    if not report_path:
        return None
    from pathlib import Path
    path = Path(report_path)
    path = path if path.is_absolute() else instance.root / path
    if path.suffix != ".json":
        path = path.with_suffix(".json")
    if not path.is_file():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    pages = [(report.get("stopped_by") or {}).get("page")] + [r.get("page") for r in report.get("layers", [])
                                                             if r.get("outcome") == DISPUTED or r.get("page")]
    corpus = {p.slug: p for p in instance.corpus()[0]}
    by_url = {p.url: p for p in corpus.values()}
    for ref in pages:
        page = corpus.get(ref or "") or by_url.get(ref or "")
        if page:
            return {"slug": page.slug, "url": page.url, "title": page.title, "text": page.text}
    return None


def make_task(instance, tid: str, *, confirm=None, unattended=False) -> dict:
    from .queue import _find, load
    topic = _find(load(instance), tid)
    gate = topic.get("gate") or {}
    if gate.get("verdict") != DISPUTED:
        raise GateRefused(f"тема {tid}: гейт — «{gate.get('verdict')}», судье нечего решать (нужно «спорно»)")
    sections, kind = nearest(instance, tid, topic["formulations"], confirm=confirm, unattended=unattended)
    risk = _risk_page(instance, topic)
    context = {"id": tid, "date": instance.today.isoformat(), "kind": kind,
               "sections": [{k: s[k] for k in ("id", "url", "title", "heading", "text")} for s in sections],
               "risk": risk["url"] if risk else ""}
    (instance.brief_dir(tid) / CONTEXT).write_text(json.dumps(context, ensure_ascii=False, indent=1),
                                                   encoding="utf-8", newline="\n")
    body = ["## Задача", "",
            f"Тема «{topic['topic']}» ({tid}): гейт сказал «спорно». Реши только по разделам ниже — пишется ли тема "
            "отдельной статьёй и с каким углом. Другие источники не используются. Не уверен — `disputed`, решит "
            "человек.", "", "## Формулировки", *[f"- {f}" for f in topic["formulations"]], "",
            f"## Ближайшие разделы корпуса ({kind})"]
    for s in sections:
        body += ["", f"### {s['id']} — {s['title']}: {s['heading']}", f"Адрес: {s['url']}", "", s["text"][:4000]]
    if risk:
        body += ["", f"## Статья-риск целиком — {risk['title']} ({risk['url']})",
                 "Каких подтем темы в ней нет — это и есть угол.", "", risk["text"][:20000]]
    path = tasks.write_task(instance, tid, "judge", "judge", "\n".join(body), result=ANSWER, schema=SCHEMA)
    return {"task": str(path), "kind": kind, "sections": len(sections), "risk": context["risk"]}


def check(instance, tid: str, *, dry_run: bool = False) -> dict:
    folder = instance.brief_dir(tid)
    if not (folder / CONTEXT).is_file():
        raise GateRefused(f"тема {tid}: задания судье нет — `judge task {tid}`")
    if not (folder / ANSWER).is_file():
        raise GateRefused(f"тема {tid}: ответа судьи нет — {folder / ANSWER}")
    context = json.loads((folder / CONTEXT).read_text(encoding="utf-8"))
    try:
        answer = json.loads((folder / ANSWER).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise EngineError(f"{ANSWER}: не JSON — {exc}") from exc
    problems = []
    verdict = answer.get("verdict")
    if verdict not in VERDICTS:
        problems.append(f"verdict «{verdict}» не из {VERDICTS}")
    section = next((s for s in context["sections"] if s["id"] == answer.get("section_id")), None)
    if section is None:
        problems.append(f"раздел «{answer.get('section_id')}» не из пятёрки задания")
    elif not str(answer.get("quote") or "").strip() or _norm(answer["quote"]) not in _norm(section["text"]):
        problems.append("цитаты нет в тексте раздела")
    if verdict == "free_with_angle" and not str(answer.get("angle") or "").strip():
        problems.append("«свободно» без угла")
    result = {"id": tid, "verdict": verdict, "accepted": not problems, "problems": problems,
              "section": section["id"] if section else "", "kind": context["kind"]}
    if problems or dry_run:
        return result
    row = {"date": instance.today.isoformat(), "id": tid, "verdict": verdict, "section": section["id"],
           "url": section["url"], "quote": answer["quote"], "angle": answer.get("angle", ""), "pentad": context["kind"],
           "model_used": answer.get("model_used", ""), "by": "судья"}
    store.append_jsonl(instance.file("judgements"), [row])
    tokens = answer.get("tokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
        spend.add(instance, tid, "судья", "tokens", tokens, FACT, "ответ судьи")
    else:
        spend.add(instance, tid, "судья", "tokens", None, NO_DATA, "ответ судьи")
    if verdict != "disputed":
        result["queue"] = apply_verdict(instance, tid, "free" if verdict == "free_with_angle" else "closed",
                                        answer.get("angle", ""), section, "судья")
    return result


def apply_verdict(instance, tid: str, verdict: str, angle: str, section: dict | None, who: str) -> str:
    from . import queue as queue_mod
    from .queue import candidate_of
    topics = queue_mod.load(instance)
    topic = queue_mod._find(topics, tid)
    if verdict == "free":
        if not (topic.get("gate") or {}).get("complete"):
            raise GateRefused(f"тема {tid}: гейт неполный — бронь по решению судьи невозможна, нужен полный гейт")
        from . import reservations
        cand = candidate_of(instance, tid)
        record = reservations.create(instance, topic=cand.topic, key=cand.key(instance.norm).to_list(),
                                     cluster_id=cand.cluster_id, who=who, queue_id=tid)
        topics = queue_mod.load(instance)
        topic = queue_mod._find(topics, tid)
        topic.update(status=queue_mod.GATED, reservation_id=record["id"], angle=angle,
                     judge={"verdict": "свободно", "by": who, "date": instance.today.isoformat()})
        return f"бронь {record['id']}, тема gated с углом; " + queue_mod.save(instance, topics)
    reason = f"закрыто разделом {section['id']}" if section else "закрыто — решение человека"
    topic.update(status=queue_mod.REJECTED, rejected={"layer": "судья", "reason": reason,
                                                      "page": section["url"] if section else "",
                                                      "section": section["heading"] if section else ""},
                 judge={"verdict": "закрыто", "by": who, "date": instance.today.isoformat()})
    text = queue_mod.save(instance, topics)
    store.append_jsonl(instance.file("rejections"), [{
        "date": instance.today.isoformat(), "topic": topic["topic"], "queue_id": tid, "verdict": "пересекается",
        "layer": "судья", "reason": reason, "page": section["url"] if section else "",
        "section": section["heading"] if section else "", "full_gate": True}])
    return "тема снята; " + text


def accept(instance, tid: str, who: str, verdict: str, angle: str = "") -> str:
    if verdict == "free" and not angle.strip():
        raise EngineError("--verdict free — нужен --angle: угол статьи")
    store.append_jsonl(instance.file("judgements"), [{
        "date": instance.today.isoformat(), "id": tid, "verdict": verdict, "section": "", "url": "", "quote": "",
        "angle": angle, "pentad": "", "model_used": "", "by": who}])
    return apply_verdict(instance, tid, verdict, angle, None, who)


def register(sub):
    from ..cli import add_profile
    from ..core.budget import add_flags
    parser = sub.add_parser("judge", help="судья спорного: task | check | accept")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("task", help="задание судье по теме со «спорно»")
    add_profile(p)
    p.add_argument("id")
    add_flags(p)
    p.set_defaults(func=run)
    p = actions.add_parser("check", help="проверить ответ судьи кодом и записать итог")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--dry-run", action="store_true", help="только печать вердикта, без записи")
    p.set_defaults(func=run)
    p = actions.add_parser("accept", help="решение человека по «спорно»")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--who", required=True)
    p.add_argument("--verdict", required=True, choices=("free", "closed"))
    p.add_argument("--angle", default="")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "task":
        out = make_task(instance, args.id, confirm=args.confirm_spend, unattended=args.unattended)
        print(f"задание судье: {out['task']} — разделов {out['sections']} ({out['kind']})"
              + (f"; статья-риск {out['risk']}" if out["risk"] else ""))
        return 0
    if args.action == "check":
        out = check(instance, args.id, dry_run=args.dry_run)
        for line in out["problems"]:
            print(f"  ✗ {line}")
        if not out["accepted"]:
            print(f"Ответ судьи по {args.id} не принят")
            return 1
        print(f"Судья по {args.id}: {out['verdict']} (раздел {out['section']}, {out['kind']})"
              + (f"; {out['queue']}" if out.get("queue") else "") + (" — без записи (--dry-run)" if args.dry_run else ""))
        return 2 if out["verdict"] == "disputed" else 0
    print(accept(instance, args.id, args.who, args.verdict, args.angle))
    return 0
