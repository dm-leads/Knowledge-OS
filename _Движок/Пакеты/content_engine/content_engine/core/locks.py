"""Блокировка от двойного прогона (Т16.4): файл `locks/<id>-<действие>.lock` на тему и действие.

Файл создаётся атомарно (`O_CREAT | O_EXCL`): из двух одновременных прогонов проходит один. Живая блокировка —
`LockBusy` (код 1), ни одного запроса. Блокировка старше `publish.lock_minutes` считается брошенной: снимается с
записью `lock_removed` в журнал выпуска. Снимает блокировку тот, кто её поставил, — выходом из `with`; чужую (её
перехватили, пока шёл долгий прогон) не трогает.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import publications
from .errors import LockBusy


def path_of(instance, tid: str, action: str):
    folder = instance.root / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{tid}-{action}.lock"


def _read(path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _create(path, record: dict) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record))
    return True


def acquire(instance, tid: str, action: str):
    path = path_of(instance, tid, action)
    now = instance.now()
    record = {"time": now.isoformat(timespec="seconds"), "pid": os.getpid()}
    for _ in range(2):
        if _create(path, record):
            return path
        data = _read(path)
        try:
            since = datetime.fromisoformat(data["time"])
        except (KeyError, ValueError):
            since = datetime.min
        limit = timedelta(minutes=instance.p["publish"]["lock_minutes"])
        if now - since < limit:
            raise LockBusy(f"тема {tid}, действие «{action}»: идёт другой прогон с {data.get('time')} "
                           f"(процесс {data.get('pid')}); блокировка снимется через "
                           f"{instance.p['publish']['lock_minutes']} мин после начала")
        # снять брошенную атомарно: переименовать и убедиться, что сняли именно прочитанную, а не свежую чужую
        stale = path.with_name(f"{path.name}.stale-{os.getpid()}")
        try:
            os.replace(path, stale)
        except FileNotFoundError:
            continue
        if _read(stale) != data:
            if not path.exists():
                os.replace(stale, path)
            else:
                stale.unlink()
            raise LockBusy(f"тема {tid}, действие «{action}»: блокировку перехватил другой прогон")
        stale.unlink()
        publications.log(instance, "lock_removed", id=tid, action=action, since=data.get("time", ""),
                         reason=f"старше {instance.p['publish']['lock_minutes']} мин")
    raise LockBusy(f"тема {tid}, действие «{action}»: блокировку перехватил другой прогон")


@contextmanager
def held(instance, tid: str, action: str):
    path = acquire(instance, tid, action)
    mine = _read(path)
    try:
        yield path
    finally:
        if path.is_file() and _read(path) == mine:
            path.unlink()
