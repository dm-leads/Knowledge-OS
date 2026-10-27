"""«Что с машиной» (шаг 4): `content-engine status --profile [--topic ID]` — только чтение.

Без пробы сети и без побочной записи: брони не освобождаются, папки не создаются, отчёты не пишутся (инстанс открыт
`read_only`). Показывает: профиль, запас очереди в днях, совет по темпу, расход за 30 дней, ближайшие замеры книги ставок,
последние выпуски. С `--topic` — одна тема: статус, бриф и утверждение, пакет, «доехало» и день выкладки, заявки и
статус индексации.
"""
from __future__ import annotations

import json
from datetime import date


def _topic(instance, tid: str) -> list[str]:
    from ..core import approval, publications
    from . import queue as queue_mod
    lines = [f"Тема {tid}"]
    try:
        topic = queue_mod._find(queue_mod.load(instance), tid)
        lines.append(f"  статус: {topic['status']}; тема: {topic['topic']}; направление: {topic.get('direction', '—')}")
        bet = topic.get("bet") or {}
        lines.append("  ставка: " + (f"{bet.get('hypothesis_id') or 'вручную'} — {bet.get('expectation')}"
                                     if bet.get("expectation") else "нет"))
    except Exception as exc:                              # тема не из очереди — показывается, что есть
        lines.append(f"  в очереди нет: {exc}")
    folder = instance.brief_dir(tid, create=False)
    if folder.is_dir():
        approved = (folder / approval.APPROVAL).is_file()
        lines.append(f"  бриф: {'есть' if (folder / approval.BRIEF).is_file() else 'нет'}, "
                     f"{'утверждён' if approved else 'не утверждён'}; текст: "
                     f"{'есть' if (folder / 'article.md').is_file() else 'нет'}")
        manifest = folder / "package" / "manifest.json"
        if manifest.is_file():
            m = json.loads(manifest.read_text(encoding="utf-8"))
            lines.append(f"  пакет: собран {m.get('built')}, {'готов' if m.get('ready') else 'НЕ готов'}")
    else:
        lines.append("  рабочей папки темы нет")
    first = publications.published(instance, tid=tid)
    if first:
        lines.append(f"  доехало: да; день выкладки {first['day']}" +
                     (f" ({first['published_source']})" if first.get("published_source") else "") + f"; {first['url']}")
    else:
        lines.append("  доехало: нет записи")
    for row in publications.read(instance):
        if row.get("id") == tid and row["event"] == "indexing":
            lines.append(f"  заявка на индексацию: {row.get('system')} {row.get('day', '')} — "
                         f"{'подтверждена' if row.get('confirmed') else 'не подтверждена'}")
        if row.get("id") == tid and row["event"] == "index_status":
            lines.append(f"  индекс на {row.get('age')}-й день: Google {'да' if row.get('google_in') else 'нет'}, "
                         f"Яндекс {'да' if row.get('yandex_in') else 'нет'}")
    return lines


def report(instance, topic: str | None = None) -> list[str]:
    from ..core import bets, publications, spend
    from . import queue as queue_mod
    from .onboard import pace
    p = instance.p
    lines = [f"Контент-машина: {p['site']['domains'][0]}, профиль версии {p['schema_version']}"]
    try:
        out, _ = queue_mod.status(instance)
        lines.append(f"Запас: {out['stock']} тем ≈ {out['days']} дн. (норма {out['norm']}); по статусам: "
                     + ", ".join(f"{k} — {v}" for k, v in out["by_status"].items()))
    except Exception as exc:
        lines.append(f"Запас: {exc}")
    pc = pace(instance)
    lines.append(f"Темп: возраст домена {pc['domain_age']}; индексация: {pc['indexing_speed']}"
                 + (f"; ! {pc['warning']}" if pc["warning"] else ""))
    lines.append("Расход за 30 дней:\n" + "\n".join("  " + line for line in spend.render(spend.report(instance, 30))
                                                    .splitlines()))
    upcoming = []
    for b in bets.load(instance):
        for window, day in (b.get("measure") or {}).items():
            if date.fromisoformat(day) >= instance.today:
                upcoming.append((day, window, b["id"], b.get("hypothesis_id") or "без гипотезы"))
    upcoming.sort()
    lines.append("Ближайшие замеры ставок: " + ("; ".join(f"{d} — {t} (+{w}, {h})" for d, w, t, h in upcoming[:5])
                                             or "нет"))
    last = [r for r in publications.read(instance) if r["event"] == "published"][-5:]
    lines.append("Последние выпуски: " + ("; ".join(f"{r['day']} {r.get('id')} {r['url']}" for r in last) or "нет"))
    if topic:
        lines += _topic(instance, topic)
    return lines


def register(sub):
    from ..cli import add_profile
    p = sub.add_parser("status", help="что с машиной: запас, темп, расход, замеры, выпуски; --topic — одна тема "
                                      "(только чтение)")
    add_profile(p)
    p.add_argument("--topic", help="id темы: статус, бриф, пакет, «доехало», индексация")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args, read_only=True)
    print("\n".join(report(instance, args.topic)))
    return 0
