"""План работ на неделю (Т42.3): `content-engine week plan` — 5–8 задач из узких мест машины.

Кандидаты — красное и тревоги последнего пульса, «не доехало» и «не проверено» из журнала выпуска, запас ниже нормы,
ставки на замере в ближайшие 14 дней и принятые итоги без предложения в Движок, предложения «закрыть направление»,
утверждённые правки аудита и сироты, «не подключено» из самопроверки. У каждой задачи — что сделать (команда), зачем,
число-основание со ссылкой на файл и объём. Порядок: красное пульса → деньги (ставки, экономика) → запас → аудит →
подключения. Узких мест меньше пяти — честно меньше, с пометкой.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from ..core import audit_store, bets, publications

ORDER = ("пульс", "деньги", "запас", "аудит", "подключение")


def candidates(instance) -> list[dict]:
    out = []

    def task(group, what, why, number, source, size):
        out.append({"group": group, "what": what, "why": why, "number": number, "source": source, "size": size})

    pulse_dir = instance.root / "data" / "pulse"
    last = sorted(pulse_dir.glob("????-??-??.json")) if pulse_dir.is_dir() else []
    if last:
        data = json.loads(last[-1].read_text(encoding="utf-8"))
        v = data["verdict"]
        if v["color"] != "зелёный":
            task("пульс", v["todo"] or "разобрать вердикт", f"вердикт {v['color']}: {v['why']}", f"правило {v['rule']}",
                 str(last[-1]), "средняя")
        for a in data["blocks"].get("positions", {}).get("alarms", [])[:3]:
            if a["kind"] == "падение" or a["kind"].startswith("выход"):
                task("пульс", f"разобрать «{a['query']}» — {a['url']}", f"{a['engine']}: {a['kind']}",
                     f"позиция {a['was']} → {a['now']}", str(last[-1]), "малая")
    rows = publications.read(instance)
    for r in rows[-50:]:
        if r["event"] == "unverified":
            task("пульс", f"`publish verify {r.get('id')}` повторить", "«доехало» не проверено — сеть не ответила",
                 r.get("reason", ""), "data/publications.jsonl", "малая")
    soon = (instance.today + timedelta(days=14)).isoformat()
    due, accepted = 0, 0
    for rec in bets.load(instance):
        for w, day in (rec.get("measure") or {}).items():
            if day and instance.today.isoformat() <= day <= soon and w not in (rec.get("measures") or {}):
                due += 1
        for w, m in (rec.get("measures") or {}).items():
            if m.get("result") is None and m.get("proposed"):
                accepted += 1
            if m.get("result") in bets.RESULTS and rec.get("hypothesis_id") and not m.get("proposed_file"):
                task("деньги", f"`bet propose` — итог {rec['id']} +{w} в Движок роста", "принятый итог не передан",
                     m["result"], "data/bets.json", "малая")
    if due:
        task("деньги", "`bet measure` в дни окон; проверить, что выгрузка денег за окно готова",
             "окна замера наступают в ближайшие 14 дней", f"{due} окон", "data/bets.json", "малая")
    if accepted:
        task("деньги", "`bet accept <id> --window --who` — принять итоги", "предложенные итоги ждут человека",
             f"{accepted} итогов", "data/bets.json", "средняя")
    econ = instance.reports_dir().parent
    for path in sorted(econ.glob("*/economics.md"))[-1:]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("предложение: закрыть направление"):
                task("деньги", "вынести направление на решение Движка роста", line, line.split("—", 1)[-1].strip(),
                     str(path), "средняя")
    from . import queue as queue_mod
    try:
        st, _ = queue_mod.status(instance)
        if st["stock"] < st["norm"]:
            task("запас", "`queue refill`", "запас тем ниже нормы", f"{st['stock']} из {st['norm']} (≈ {st['days']} дн.)",
                 "data/queue.json", "средняя")
    except Exception as exc:
        task("запас", "`demand collect` → `cluster` → `map build` → `queue build`", "очередь не читается", str(exc),
             "data/queue.json", "большая")
    snap = audit_store.latest(instance)
    if snap is None:
        task("аудит", "`audit run`", "аудита нет", "аудита нет", "data/audit/", "средняя")
    else:
        crit = [f for f in snap["findings"] if f["level"] == "critical"]
        if crit:
            task("аудит", "исправить критичные находки аудита", "критичные находки", f"{len(crit)} критичных",
                 f"data/audit/{snap['date']}.json", "средняя")
        orphans = [f for f in snap["findings"] if f["check"] == "orphan"]
        if orphans:
            task("аудит", "связать статьи-сироты (советы — `audit advice`)", "статьи без входящих ссылок",
                 f"{len(orphans)} сирот", f"data/audit/{snap['date']}.json", "малая")
    from .overlaps import approved
    for r in approved(instance)[:3]:
        task("аудит", f"правка по «утверждаю»: {r['url']}", r["check"], r["id"], "data/audit/decisions.jsonl", "средняя")
    from .check import OFF, run_check
    try:
        off = [i for i in run_check(instance) if i["light"] == OFF][:2]
    except Exception as exc:                     # самопроверка не помешает плану — причина в задаче
        off = [{"item": "самопроверка", "hint": "`content-engine check`", "detail": str(exc)}]
    for item in off:
        task("подключение", item.get("hint") or f"подключить: {item['item']}", f"{item['item']}: не подключено",
             item.get("detail") or "не подключено", "content-engine check", "малая")
    return out


def plan(instance) -> dict:
    p = instance.p["week_plan"]
    items = sorted(candidates(instance), key=lambda t: ORDER.index(t["group"]))
    chosen = items[:p["max"]]
    note = "" if len(chosen) >= p["min"] else f"узких мест меньше {p['min']} — задач {len(chosen)}"
    return {"tasks": chosen, "note": note, "candidates": len(items)}


def render(instance, data: dict) -> str:
    lines = [f"# План работ на неделю — {instance.today.isoformat()}", ""]
    if data["note"]:
        lines += [data["note"], ""]
    for n, t in enumerate(data["tasks"], 1):
        lines.append(f"{n}. **{t['what']}** — {t['why']}. Основание: {t['number']} ({t['source']}). Объём: {t['size']}.")
    return "\n".join(lines) + "\n"


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("week", help="план работ на неделю: plan")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("plan", help="5–8 задач из узких мест машины, у каждой — число-основание")
    add_profile(p)
    p.add_argument("--unattended", action="store_true", help="запуск без человека (расписание); платных вызовов нет")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    data = plan(instance)
    text = render(instance, data)
    (instance.reports_dir() / "week_plan.md").write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0
