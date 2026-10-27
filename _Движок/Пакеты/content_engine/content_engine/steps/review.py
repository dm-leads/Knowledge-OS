"""Критика и вычитка фактов (В4, Т29): `content-engine review task | check | accept <id>`.

- `task <id>` — два задания. Критику (роль `critic`, `task_critique.md`) — текст, бриф, правила проекта; результат —
  таблица «было → стало» в `critique.md`; его же выдаёт `write task` — задание одно на обе команды. Вычитке фактов
  (роль `fact_checker`, по умолчанию вторая модель `codex-latest`) — **отдельная папка** `data/factcheck/<id>/`: копия
  текста статьи, выдержка библиотеки фактов и задание. Брифа, заданий автора и переписки там нет, и код это
  проверяет: путь к ним в задании — ошибка, задание не пишется. Вторая модель работает без нашей памяти и MCP — всё
  нужное лежит в этой папке.
- `check <id>` — схема `claims.jsonl` (утверждение, тип, первоисточник, итог, юридический риск). Статья — черновик,
  если есть блокер, непроверенное или уточнение без решения человека, юридический риск без решения человека или
  текст изменился после вычитки: итог — `review.json` в папке темы с суммой текста, его читает гейт статьи (проверка
  `facts`). Подтверждённые утверждения пополняют библиотеку фактов без дублей.
- `accept <id> <№> --who <имя> --decision keep|remove` — решение человека по строке вычитки: принять как есть или
  убрать из текста (тогда текст правится и вычитка повторяется).
`facts.mode: human` — задание вычитки не пишется, `claims.jsonl` в той же папке заполняет человек.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..core import approval, facts, spend, store, tasks
from ..core.errors import EngineError, GateRefused
from ..core.number import NO_DATA

TYPES = ("number", "norm", "law", "product")
RESULTS = ("confirmed", "blocker", "clarify", "unverified")
FORBIDDEN = ("brief.yaml", "task_write", "task_plan", "task_critique", "critique.md", "brief_context", "judge.json",
             "approval.json", "transcript", "переписка с автором")   # переписка автора с агентом — тоже контекст
CLAIMS_SCHEMA = """# claims.jsonl — одна строка JSON на утверждение
{"n": 1, "claim": "<утверждение из текста дословно>", "type": "number | norm | law | product",
 "source": "<адрес или документ первоисточника; нет — пусто>", "result": "confirmed | blocker | clarify | unverified",
 "legal_risk": false, "note": "<что не так, если не confirmed>", "model_used": "<модель>", "tokens": 0}"""
CRITIQUE_SCHEMA = """# critique.md — таблица правок
| № | Было | Стало | Правило (ссылка на стандарт проекта или канон) |
|---|---|---|---|"""


IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")


IMAGE_URL = re.compile(r"(!\[[^\]]*\])\([^)]*\)")


def normalized_text(text: str) -> str:
    """Текст для суммы вычитки: адреса картинок и поле `image` шапки сняты, alt и всё остальное — на месте.

    Вставка картинки (`images insert`) утверждений не меняет и вычитку не сбрасывает; правка слов — и в alt тоже —
    сбрасывает. Поле `image` снимается только в шапке YAML, строка «image:» в теле — обычный текст."""
    text = text.replace("\r\n", "\n")
    head, body = "", text
    if text.startswith("---\n") and "\n---" in text[4:]:
        end = text.index("\n---", 4)
        head = "\n".join(l for l in text[:end].split("\n") if not l.startswith("image:"))
        body = text[end:]
    body = IMAGE_URL.sub(r"\1", body)
    return "\n".join(l.rstrip() for l in (head + body).split("\n")).strip()


def text_digest(path: Path) -> str:
    return hashlib.sha256(normalized_text(Path(path).read_bytes().decode("utf-8")).encode("utf-8")).hexdigest()


def factcheck_dir(instance, tid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", tid or ""):
        raise EngineError(f"id темы «{tid}»: латиница, цифры, «-» и «_»")
    path = instance.root / "data" / "factcheck" / tid
    path.mkdir(parents=True, exist_ok=True)
    return path


def _rules(instance) -> list[str]:
    return [f"- {r}" for r in instance.p["write"]["rules"]] or ["- правила проекта не заданы (write.rules)"]


def critique_task(instance, tid: str) -> Path:
    """Задание критику — одно на `write task` и `review task` (Т28.1, Т29.1)."""
    folder = instance.brief_dir(tid)
    body = ["## Задача", "",
            f"Прочитать текст `{folder / 'article.md'}` (тема {tid}) и выписать правки тона и правил проекта "
            "таблицей «было → стало»: каждая правка — с правилом, которое она чинит. Факты не проверять — это вычитка "
            "фактов, отдельный агент. Текст не переписывать целиком.", "",
            "## Опора", f"- бриф: `{folder / approval.BRIEF}`", *_rules(instance),
            "- канон: «Методология — Текст и редактура (информационный стиль, Ильяхов)», навык `text-editor`",
            f"- стоп-фразы проекта: {', '.join(instance.p['gate_article']['stop_phrases']) or 'нет'}",
            f"- шаблоны ИИ-интонации: {', '.join(instance.p['gate_article']['ai_tone_patterns']) or 'нет'}"]
    return tasks.write_task(instance, tid, "critic", "critique", "\n".join(body), result="critique.md",
                            schema=CRITIQUE_SCHEMA)


META = "factcheck.json"


def start_round(instance, tid: str) -> Path:
    """Новый круг вычитки: прежние `claims.jsonl` и `decisions.json` — в архив (решения по номерам к новому тексту не
    переносятся), копия текста байт в байт и сумма вычитанного текста — в `factcheck.json`. Во всех режимах."""
    import shutil
    article = instance.brief_dir(tid) / "article.md"
    if not article.is_file():
        raise GateRefused(f"тема {tid}: текста нет — вычитывать нечего ({article})")
    folder = factcheck_dir(instance, tid)
    old = [folder / n for n in ("claims.jsonl", "decisions.json") if (folder / n).is_file()]
    if old:
        base = folder / "archive" / instance.now().strftime("%Y%m%d-%H%M%S")
        archive, n = base, 2
        while archive.exists():                  # два круга в одну секунду — не перезаписывать архив
            archive, n = base.with_name(f"{base.name}-{n}"), n + 1
        archive.mkdir(parents=True)
        for path in old:
            shutil.move(str(path), str(archive / path.name))
    (folder / "article.md").write_bytes(article.read_bytes())
    meta = {"id": tid, "date": instance.today.isoformat(), "article_digest": text_digest(article),
            "mode": instance.p["facts"]["mode"]}
    (folder / META).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return folder


def factcheck_task(instance, tid: str) -> Path:
    article = instance.brief_dir(tid) / "article.md"
    if article.is_file():                        # утечка — до архивации прежнего круга: отказ не теряет его
        leaks = [w for w in FORBIDDEN if w in article.read_text(encoding="utf-8")]
        if leaks:
            raise EngineError(f"задание вычитки содержит путь к контексту автора {leaks} (в тексте статьи) — не "
                              "пишется, прежний круг вычитки не тронут (Т29.1)")
    folder = start_round(instance, tid)
    if instance.p["facts"]["mode"] == "human":
        raise GateRefused(f"facts.mode: human — вычитку делает человек: claims.jsonl в {folder} (копия текста там же)")
    text = (folder / "article.md").read_text(encoding="utf-8")
    library = [f for f in facts.read(instance) if facts.valid(f, instance.today)][:200]
    body = ["## Задача", "",
            "Выписать из текста статьи (`article.md` в этой папке) каждое проверяемое утверждение: число, норму "
            "(СП, СанПиН, ГОСТ), статью закона, свойство продукта. Для каждого найти первоисточник и записать итог: "
            "`confirmed` — первоисточник подтверждает; `blocker` — противоречит; `clarify` — нужна правка "
            "формулировки; `unverified` — первоисточник не найден. Юридический риск (обещание результата, сравнение с "
            "конкурентом, медицинское утверждение) — `legal_risk: true`. Работать только с этой папкой: контекста "
            "автора нет намеренно.", "",
            "## Подтверждённые факты библиотеки — годны, можно ссылаться",
            *([f"- {f['fact']}: {f['value']} {f['unit']} — {f['source']} (проверен {f['checked']})" for f in library]
              or ["- библиотека пуста"]),
            "", "## Текст статьи", "", text]
    joined = "\n".join(body)
    leaks = [w for w in FORBIDDEN if w in joined] + ([str(instance.brief_dir(tid))] if str(instance.brief_dir(tid))
                                                     in joined else [])
    if leaks:
        raise EngineError(f"задание вычитки содержит путь к контексту автора {leaks} — не пишется (Т29.1)")
    path = tasks.write_task(instance, tid, "fact_checker", "factcheck", joined, result="claims.jsonl",
                            schema=CLAIMS_SCHEMA, folder=folder)
    task = path.read_text(encoding="utf-8")
    if any(w in task for w in FORBIDDEN) or str(instance.brief_dir(tid)) in task:
        path.unlink()
        raise EngineError("задание вычитки содержит путь к контексту автора — удалено (Т29.1)")
    return path


def _claims(folder: Path) -> list[dict]:
    path = folder / "claims.jsonl"
    if not path.is_file():
        raise GateRefused(f"вычитки нет: {path}")
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise EngineError(f"claims.jsonl, строка {n}: не JSON — {exc}") from exc
        for field in ("n", "claim", "type", "source", "result"):
            if field not in row:
                raise EngineError(f"claims.jsonl, строка {n}: нет поля {field}")
        if row["type"] not in TYPES:
            raise EngineError(f"claims.jsonl, строка {n}: type «{row['type']}» не из {TYPES}")
        if row["result"] not in RESULTS:
            raise EngineError(f"claims.jsonl, строка {n}: result «{row['result']}» не из {RESULTS}")
        if row["result"] == "confirmed" and not str(row["source"]).strip():
            raise EngineError(f"claims.jsonl, строка {n}: «подтверждено» без первоисточника")
        rows.append(row)
    return rows


def _decisions(folder: Path) -> dict:
    path = folder / "decisions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _in_text(claim: str, text: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s or "").strip().lower()   # noqa: E731
    return bool(norm(claim)) and norm(claim) in norm(text)


def open_claims(rows: list[dict], decisions: dict, text: str | None = None) -> list[str]:
    """Что держит статью черновиком (Т29.2): блокер без «убрать», непроверенное и уточнение без решения человека,
    юридический риск без решения человека. Одна функция для `review check` и известных ответов."""
    out = []
    for row in rows:
        decided = decisions.get(str(row["n"]))
        removed = decided and decided["decision"] == "remove"
        if removed and text is not None and _in_text(row["claim"], text):
            out.append(f"№{row['n']} решено убрать, но утверждение ещё в тексте: {row['claim'][:80]}")
        elif row["result"] == "blocker" and not removed:
            out.append(f"№{row['n']} блокер: {row['claim'][:80]}")
        elif row["result"] in ("unverified", "clarify") and not decided:
            out.append(f"№{row['n']} {row['result']}: {row['claim'][:80]} — решение человека")
        if row.get("legal_risk") and not decided:
            out.append(f"№{row['n']} юридический риск: {row['claim'][:80]} — решение человека")
    return out


def check(instance, tid: str) -> dict:
    folder = factcheck_dir(instance, tid)
    article = instance.brief_dir(tid) / "article.md"
    meta_path = folder / META
    if not meta_path.is_file():
        raise GateRefused(f"тема {tid}: круга вычитки нет — `review task {tid}` (копия текста и сумма вычитанного)")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows = _claims(folder)
    decisions = _decisions(folder)
    open_items = []
    sha = text_digest(article) if article.is_file() else ""
    if meta.get("article_digest") != sha:
        open_items.append("текст изменился после вычитки — `review task` заново (новый круг)")
    text = article.read_text(encoding="utf-8") if article.is_file() else ""
    open_items += open_claims(rows, decisions, text)
    report = {"id": tid, "date": instance.today.isoformat(), "article_sha256": sha, "clean": not open_items,
              "open": open_items, "claims": len(rows), "confirmed": sum(r["result"] == "confirmed" for r in rows)}
    (instance.brief_dir(tid) / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                          encoding="utf-8", newline="\n")
    current = meta.get("article_digest") == sha
    confirmed = [r for r in rows if r["result"] == "confirmed"] if current else []
    added, text = facts.add(instance, [{
        "fact": r["claim"], "value": (re.findall(r"\d+(?:[.,]\d+)?", r["claim"]) or [r["claim"]])[0], "unit": "",
        "source": r["source"], "checked": instance.today.isoformat(), "ttl_days": instance.p["facts"]["ttl_days"],
        "status": "confirmed", "topic": tid} for r in confirmed]) if confirmed else (
        0, "подтверждённых нет" if current else "вычитка по прежнему тексту — в библиотеку фактов не идёт")
    report["library"] = text
    if not any(row["id"] == tid and row["step"] == "вычитка" for row in spend.rows(instance)):
        spend.add(instance, tid, "вычитка", "tokens", None, NO_DATA, "вычитка фактов")
    return report


def accept(instance, tid: str, n: int, who: str, decision: str) -> str:
    folder = factcheck_dir(instance, tid)
    if not any(int(r["n"]) == n for r in _claims(folder)):
        raise EngineError(f"строки №{n} в claims.jsonl нет")
    data = _decisions(folder)
    data[str(n)] = {"who": who, "decision": decision, "date": instance.today.isoformat()}
    path = folder / "decisions.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    if json.loads(path.read_text(encoding="utf-8")) != data:
        raise EngineError("decisions.json: записано — прочитано другим")
    return f"№{n}: {decision} ({who}); дальше — `review check {tid}`" + (
        " после правки текста и новой вычитки" if decision == "remove" else "")


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("review", help="критика и вычитка фактов: task | check | accept")
    actions = parser.add_subparsers(dest="action", required=True)
    for name, text in (("task", "задания критику и вычитке фактов"), ("check", "проверить вычитку кодом"),
                       ("accept", "решение человека по строке вычитки")):
        p = actions.add_parser(name, help=text)
        add_profile(p)
        p.add_argument("id")
        if name == "accept":
            p.add_argument("n", type=int)
            p.add_argument("--who", required=True)
            p.add_argument("--decision", required=True, choices=("keep", "remove"))
        p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "task":
        approval.require(instance, args.id)
        print(f"задание критику: {critique_task(instance, args.id)}")
        if instance.p["facts"]["mode"] == "human":
            print(f"вычитка фактов — человек: claims.jsonl в {start_round(instance, args.id)}")
        else:
            print(f"задание вычитке фактов (без контекста автора): {factcheck_task(instance, args.id)}")
        return 0
    if args.action == "check":
        out = check(instance, args.id)
        for line in out["open"]:
            print(f"  ✗ {line}")
        print(f"Вычитка {args.id}: утверждений {out['claims']}, подтверждено {out['confirmed']}; "
              + ("закрыта" if out["clean"] else "статья — черновик") + f"; {out['library']}")
        return 0 if out["clean"] else 1
    print(accept(instance, args.id, args.n, args.who, args.decision))
    return 0
