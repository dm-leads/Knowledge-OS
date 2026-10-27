"""Библиотека фактов ниши (В4, Т29.3): `data/facts.jsonl` — подтверждённые вычиткой утверждения с первоисточником.

Строка: `fact` (что утверждается), `value` (число или формулировка нормы), `unit`, `source` (адрес или документ),
`checked` (дата проверки), `ttl_days` (срок годности), `status` (`confirmed`), `topic` (тема, где подтверждён).
Пополняется только вычиткой (`review check`) и без дублей по ключу «факт + значение + источник». Факт с истёкшим
сроком не входит в пакет проверенных фактов, а число текста, совпавшее с ним, держит статью черновиком (проверка
`facts` гейта статьи) — норма могла поменяться.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import store
from .errors import EngineError

FIELDS = ("fact", "value", "unit", "source", "checked", "ttl_days", "status", "topic")


def read(instance) -> list[dict]:
    return store.read_jsonl(instance.file("facts"), missing_ok=True)


def valid(row: dict, today: date) -> bool:
    """Подтверждён и не просрочен."""
    if row.get("status") != "confirmed":
        return False
    return date.fromisoformat(row["checked"]) + timedelta(days=int(row["ttl_days"])) >= today


def expired(instance) -> list[dict]:
    return [r for r in read(instance) if r.get("status") == "confirmed" and not valid(r, instance.today)]


def pack(instance, topic_id: str) -> list[dict]:
    """Пакет проверенных фактов темы (Т28.3): подтверждённые и годные факты этой темы."""
    return [r for r in read(instance) if r.get("topic") == topic_id and valid(r, instance.today)]


def _key(row: dict) -> tuple:
    return (str(row["fact"]).strip().lower(), str(row["value"]).strip().lower(), str(row["source"]).strip().lower())


def add(instance, rows: list[dict]) -> tuple[int, str]:
    """Добавить подтверждённые факты без дублей; вернуть (сколько добавлено, строка сверки)."""
    for row in rows:
        missing = [f for f in FIELDS if f not in row]
        if missing:
            raise EngineError(f"факт {row.get('fact')!r}: нет полей {missing}")
    known = {_key(r) for r in read(instance)}
    new = []
    for row in rows:
        if _key(row) not in known:
            known.add(_key(row))
            new.append({k: row[k] for k in FIELDS})
    if not new:
        return 0, "библиотека фактов: новых нет"
    return len(new), store.append_jsonl(instance.file("facts"), new)
