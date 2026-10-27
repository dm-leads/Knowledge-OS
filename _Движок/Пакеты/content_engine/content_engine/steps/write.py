"""Текст (В3, Т28): `content-engine write task <id>` — задания ролей по утверждённому брифу; пакет текст не пишет.

Запрет в коде: без утверждённого брифа задания не выдаются (Т19.6, Т28.1). Три задания: `planner` — план разделов
(`task_plan.md` → `plan.md`), `writer` — текст (`task_write.md` → `article.md`), `critic` — критика (`task_critique.md`
→ `critique.md`, то же задание выдаёт `review task`). Усиленный уровень текста (поле брифа `level`, по умолчанию
`write.level`, в нише — не ниже `niche.level`) требует пакета проверенных фактов темы до задания писателю (Т28.3).
Результат проверяет гейт статьи: ритм, ИИ-интонация, объём, ссылки на нормы и свои статьи, прямые ответы (Т28.2).
"""
from __future__ import annotations

import json

from ..core import approval, facts, store, tasks
from ..core.errors import GateRefused
from .brief import CONTEXT, load_brief
from .review import critique_task

PLAN_SCHEMA = """# plan.md — план разделов по брифу
## <H2 из брифа>
- прямой ответ (первый абзац, до N слов): <о чём>
- что в разделе: <пункты, факты, таблица или список>
- ссылки: <адреса из кандидатов перелинковки>"""
WRITE_SCHEMA = """# article.md — статья с шапкой YAML
---
title: <title брифа>
description: <description брифа>
slug: <slug брифа>
---
# <H1 брифа>
<лид>
## <H2 брифа> …
## <FAQ — заголовок блока частых вопросов профиля>"""


def level_of(instance, brief: dict) -> str:
    level = brief.get("level") or instance.p["write"]["level"]
    if instance.p["niche"]["level"] == "усиленный":
        return "усиленный"
    return level


def make_tasks(instance, tid: str) -> dict:
    approval.require(instance, tid)
    from .prepare import topic_of
    from ..core.bets import NO_BET, bet_missing
    topic = topic_of(instance, tid)
    brief = load_brief(instance, tid)
    folder = instance.brief_dir(tid)
    level = level_of(instance, brief)
    pack = facts.pack(instance, tid)
    if level == "усиленный" and not pack:
        raise GateRefused(f"тема {tid}: усиленный уровень текста без пакета проверенных фактов — сначала факты "
                          "(вычитка и библиотека фактов), задание писателю не выдаётся")
    notes = [NO_BET.replace("<id>", tid)] if bet_missing(topic) else []
    top = store.read_json(folder / "top.json", "topic_top") if (folder / "top.json").is_file() else {}
    numbers = top.get("numbers") or {}
    context = json.loads((folder / CONTEXT).read_text(encoding="utf-8")) if (folder / CONTEXT).is_file() else {}
    g = instance.p["gate_article"]
    direction = next((d for d in instance.p["directions"] if d["name"] == topic.get("direction")), None)
    corridor = direction["volume"] if direction else None
    common = [f"- бриф (утверждён): `{folder / approval.BRIEF}`", f"- разбор топа: `{folder / 'top.json'}`",
              *_rules(instance),
              "- канон: «Методология — Текст и редактура (информационный стиль, Ильяхов)», навык `text-editor`"]
    plan = tasks.write_task(instance, tid, "planner", "plan", "\n".join([
        "## Задача", "", f"План разделов статьи «{brief.get('h1')}» строго по брифу: разделы и FAQ — из брифа, у "
        "каждого раздела — прямой ответ первым абзацем, что в нём и куда ссылки.", "", "## Опора", *common]),
        result="plan.md", schema=PLAN_SCHEMA)
    links = context.get("links", [])
    body = ["## Задача", "",
            f"Написать статью «{brief.get('h1')}» ({tid}) по утверждённому брифу и плану `{folder / 'plan.md'}`. "
            f"Угол — {brief.get('angle') or topic.get('angle') or 'из брифа'}. Уровень текста — {level}.", "",
            "## Опора", *common,
            f"- объём: цель {(numbers.get('volume_chars') or {}).get('value', '—')} знаков без разметки"
            + (f", коридор направления {corridor[0]}–{corridor[1]}" if corridor else ""),
            f"- картинок у лидеров выдачи: {(numbers.get('images') or {}).get('value', '—')} (места ставит `images plan`)",
            f"- стоп-фразы: {', '.join(g['stop_phrases']) or 'нет'}",
            "", "## Ссылки — только из кандидатов перелинковки",
            *([f"- {c['url']} — {c.get('title') or c['kind']}" + (" (нужна: подзаголовок задевает её ключ)"
                                                                   if c["kind"] == "по подзаголовку" else "")
               for c in links] or ["- кандидатов нет"]),
            "", "## Что проверит гейт статьи",
            f"- первый абзац каждого раздела — прямой ответ не длиннее {g['answer_words']} слов, со словами заголовка;",
            "- ритм как у человека: абзацы и предложения разной длины;",
            "- каждая норма (СП, СанПиН, ГОСТ, закон) — со ссылкой на первоисточник в том же абзаце;",
            "- ссылки на свои статьи — только на статьи сайта; FAQ на месте; картинки — с alt."]
    if level == "усиленный":
        body += ["", "## Пакет проверенных фактов — числа и нормы только отсюда",
                 *[f"- {f['fact']}: {f['value']} {f['unit']} — {f['source']}" for f in pack]]
    write = tasks.write_task(instance, tid, "writer", "write", "\n".join(body), result="article.md",
                             schema=WRITE_SCHEMA)
    critic = critique_task(instance, tid)
    return {"tasks": [str(plan), str(write), str(critic)], "level": level, "notes": notes}


def _rules(instance) -> list[str]:
    return [f"- правило проекта: `{r}`" for r in instance.p["write"]["rules"]] or ["- правила проекта не заданы (write.rules)"]


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("write", help="текст: task — задания ролей по утверждённому брифу")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("task", help="задания planner, writer, critic")
    add_profile(p)
    p.add_argument("id")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    out = make_tasks(instance, args.id)
    for path in out["tasks"]:
        print(f"задание: {path}")
    for note in out["notes"]:
        print(f"  ! {note}")
    print(f"уровень текста: {out['level']}; дальше — текст в article.md, `review task {args.id}`, "
          f"`gate article --topic {args.id}`")
    return 0
