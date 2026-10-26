"""Журнал выпуска `data/publications.jsonl` (Т22.5, Т24.2): события по темам, только добавлением.

События: `published` (первое «доехало»: день выкладки, адрес, день сборки), `verify` (каждая сверка), `indexing`
(заявка в систему индексации и ответ), `lock_removed` (снята брошенная блокировка), `record` (записи дня выкладки),
`rollback` (откат). День выкладки берётся отсюда, а не из файла пакета: он пишется в день действия.
"""
from __future__ import annotations

from . import store


def read(instance) -> list[dict]:
    return store.read_jsonl(instance.file("publications"), missing_ok=True)


def log(instance, event: str, **fields) -> str:
    row = {"event": event, "at": instance.now().isoformat(timespec="seconds"), **fields}
    return store.append_jsonl(instance.file("publications"), [row])


def published(instance, tid: str | None = None, url: str | None = None) -> dict | None:
    """Первая запись «доехало» темы или адреса — день выкладки."""
    for row in read(instance):
        if row["event"] == "published" and ((tid and row.get("id") == tid) or (url and row.get("url") == url)):
            return row
    return None


def last_publication_day(instance) -> str | None:
    days = [row["day"] for row in read(instance) if row["event"] == "published"]
    return max(days) if days else None
