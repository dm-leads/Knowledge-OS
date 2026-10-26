"""Учёт расхода (Т17.3–Т17.4, Р21): `data/spend.jsonl`, строка на действие, только добавлением.

Виды: `rub` — рубли (платный вызов пакета или трата вне машины, внесённая руками), `keysso_objects` — объекты квоты
Keys.so по счётчику плана выдачи, `tokens` — токены задания агента, если агент их сообщил. Статус числа — факт,
оценка или нет данных; «нет данных» не несёт значения и в сумме не становится нулём — отчёт называет его.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from . import store
from .errors import EngineError
from .number import ESTIMATE, FACT, NO_DATA

KINDS = ("rub", "keysso_objects", "tokens")
STATUSES = (FACT, ESTIMATE, NO_DATA)


def add(instance, topic_id: str, step: str, kind: str, value, status: str, source: str = "") -> str:
    if kind not in KINDS:
        raise EngineError(f"вид расхода «{kind}» не из {KINDS}")
    if status not in STATUSES:
        raise EngineError(f"статус расхода «{status}» не из {STATUSES}")
    if status == NO_DATA and value is not None:
        raise EngineError("«нет данных» не несёт значения — это не ноль")
    if status != NO_DATA and (value is None or isinstance(value, bool) or float(value) < 0):
        raise EngineError(f"расход {kind}: значение {value!r} при статусе «{status}»")
    row = {"date": instance.today.isoformat(), "id": topic_id, "step": step, "kind": kind, "value": value,
           "status": status, "source": source}
    return store.append_jsonl(instance.file("spend"), [row])


def rows(instance) -> list[dict]:
    return store.read_jsonl(instance.file("spend"), missing_ok=True)


def month_rub(instance, day: date | None = None) -> float:
    """Рубли календарного месяца по журналу — для месячного потолка бюджет-гейта."""
    day = day or instance.today
    prefix = day.isoformat()[:7]
    return sum(float(r["value"]) for r in rows(instance)
               if r["kind"] == "rub" and r["status"] != NO_DATA and r["date"].startswith(prefix))


def report(instance, days: int = 30) -> dict:
    since = (instance.today - timedelta(days=days - 1)).isoformat()
    by_topic: dict[str, dict] = defaultdict(lambda: {k: {"value": 0.0, "estimate": False, "no_data": 0}
                                                     for k in KINDS})
    total = {k: {"value": 0.0, "estimate": False, "no_data": 0} for k in KINDS}
    for r in rows(instance):
        if r["date"] < since:
            continue
        for bucket in (by_topic[r["id"]][r["kind"]], total[r["kind"]]):
            if r["status"] == NO_DATA:
                bucket["no_data"] += 1
            else:
                bucket["value"] += float(r["value"])
                bucket["estimate"] |= r["status"] == ESTIMATE
    return {"since": since, "to": instance.today.isoformat(), "topics": dict(by_topic), "total": total}


def _cell(bucket: dict, unit: str) -> str:
    value = bucket["value"]
    text = f"{value:.0f} {unit}" if value or not bucket["no_data"] else "—"
    if bucket["estimate"]:
        text += " (оценка)"
    if bucket["no_data"]:
        text += f"; нет данных: {bucket['no_data']}"
    return text


def render(data: dict) -> str:
    units = {"rub": "₽", "keysso_objects": "объектов", "tokens": "токенов"}
    lines = [f"Расход {data['since']} — {data['to']}",
             "Месячный потолок видит только траты в журнале машины; траты вне машины вносятся `spend add`.", ""]
    for tid, kinds in sorted(data["topics"].items()):
        lines.append(f"  {tid}: " + "; ".join(f"{k} {_cell(kinds[k], units[k])}" for k in KINDS))
    lines.append("Итого: " + "; ".join(f"{k} {_cell(data['total'][k], units[k])}" for k in KINDS))
    return "\n".join(lines)
