"""Книга ставок `data/bets.json` (Т23.1): статья → ставка → день выкладки → даты замеров 90 и 180 дней.

Замер и итог ставки — волна 3 (Т39); здесь — запись в день выкладки, чтобы замер считался от дня выкладки, а не сборки.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import store

WINDOWS = (90, 180)


def load(instance) -> list[dict]:
    return store.read_json(instance.file("bets"), "bets", missing_ok=True)["records"]


def add(instance, topic: dict, url: str, day: str) -> tuple[bool, str]:
    """Добавить ставку темы, если её ещё нет. Вернуть (добавлена ли, строка сверки)."""
    records = load(instance)
    if any(r["id"] == topic["id"] for r in records):
        return False, "книга ставок: запись уже есть"
    bet = topic.get("bet") or {}
    published = date.fromisoformat(day)
    records.append({"id": topic["id"], "topic": topic["topic"], "url": url, "hypothesis": bet.get("hypothesis", ""),
                    "hypothesis_id": bet.get("hypothesis_id", ""), "expectation": bet.get("expectation", ""),
                    "fail_threshold": bet.get("fail_threshold", ""), "published": day,
                    "measure": {str(w): (published + timedelta(days=w)).isoformat() for w in WINDOWS},
                    "result": None})
    return True, store.write_json(instance.file("bets"), "bets", records)
