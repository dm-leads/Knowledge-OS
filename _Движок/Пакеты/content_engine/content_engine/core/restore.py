"""Откат по виду снимка «до» (Т16.5, З36): не только через публикатор, поэтому работает и в ручном режиме.

У записей, которые делает не публикатор (передача статьи в папку проекта, загрузка картинки), свой восстановитель:
вид действия → функция, которая по снимку строит действие отката. `publish rollback` откатывает сначала такие снимки,
публикатору отдаёт только остальные; снимок неизвестного вида без публикатора — строка «откатить вручную», не ошибка.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from .actions import Action

RESTORERS: dict[str, Callable] = {}


def register(kind: str):
    def wrap(fn):
        RESTORERS[kind] = fn
        return fn
    return wrap


def _load_all():
    """Восстановители других модулей регистрируются при их импорте (загрузка картинки — `images/base.py`)."""
    try:
        from ..images import base   # noqa: F401
    except ImportError:
        pass


def split(snaps: list[dict]) -> tuple[list[dict], list[dict]]:
    _load_all()
    own = [s for s in snaps if s["action"] in RESTORERS]
    return own, [s for s in snaps if s["action"] not in RESTORERS]


def actions(instance, snaps: list[dict]) -> tuple[list[Action], list[str]]:
    """Действия отката и строки «вручную»: восстановитель, который удалять не умеет, возвращает инструкцию."""
    _load_all()
    out, by_hand = [], []
    for snap in snaps:
        made = RESTORERS[snap["action"]](instance, snap)
        if isinstance(made, str):
            by_hand.append(f"откатить вручную: {made}")
        else:
            out += made if isinstance(made, list) else [made]
    return out, by_hand


@register("article_transfer")
def _transfer(instance, snap: dict) -> Action:
    folder = Path(snap["meta"].get("folder") or snap["target"])
    created = [name for name, sha in (snap["content"] or {}).items() if sha is None]

    def do():
        for name in created:
            path = folder / name
            if path.is_file():
                path.unlink()
        if not snap["meta"].get("folder_existed") and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
        return folder

    def verify(_):
        left = [name for name in created if (folder / name).exists()]
        return (not left, f"удалено созданное машиной: {created}" if not left else f"остались: {left}")

    def snapshot():
        return {"exists": folder.is_dir(), "read_from": str(folder),
                "content": {name: (hashlib.sha256((folder / name).read_bytes()).hexdigest()
                                   if (folder / name).is_file() else None) for name in created}}

    return Action("article_transfer_rollback", str(folder), "delete",
                  f"удалить файлы, которые машина положила в папку проекта: {created}", "read_back", ("paths", "write"),
                  do=do, verify=verify, snapshot=snapshot)
