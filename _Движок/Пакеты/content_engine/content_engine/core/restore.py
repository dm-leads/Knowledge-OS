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
# Ступени прав, которые может запросить действие отката (О3 волны 3): ключ профиля и ступень. Контракт
# `publish rollback` (`write.right`) перечисляет ровно их — тест каталога сверяет; действие с другой ступенью
# в план отката не попадает.
RIGHTS = (("paths", "write"), ("images", "files"), ("images", "write"), ("publish", "commit"),
          ("publish", "publish"), ("records", "write"))


def right_text(right: tuple[str, str]) -> str:
    return f"{right[0]}.allow = {right[1]}"


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
        for item in (made if isinstance(made, list) else [made]):
            if isinstance(item, str):
                by_hand.append(f"откатить вручную: {item}")
            else:
                out.append(item)
    return out, by_hand


@register("article_transfer")
def _transfer(instance, snap: dict):
    folder = Path(snap["meta"].get("folder") or snap["target"])
    sent = snap["meta"].get("sent") or {}
    created, edited = [], []
    for name, sha in (snap["content"] or {}).items():
        if sha is not None:
            continue
        path = folder / name
        if path.is_file() and sent.get(name) and hashlib.sha256(path.read_bytes()).hexdigest() != sent[name]:
            edited.append(name)              # после передачи файл правили — машина его не удаляет
        else:
            created.append(name)
    if edited and not created:
        return f"{folder}: файлы {edited} правили после передачи — удалить вручную, если нужно"

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

    action = Action("article_transfer_rollback", str(folder), "delete",
                    f"удалить файлы, которые машина положила в папку проекта: {created}", "read_back",
                    ("paths", "write"), do=do, verify=verify, snapshot=snapshot)
    if edited:
        return [action, f"{folder}: файлы {edited} правили после передачи — удалить вручную, если нужно"]
    return action
