"""Снимок «до» и откат (Т16.5, Р14).

Перед записью в живую страницу, CMS или базу проекта адаптер читает текущее состояние того, что изменит, и кладёт его
в `data/snapshots/before/<id>/<run_id>/<n>.json`: что читал, откуда, когда, было ли что-то по адресу и что именно. Нет
снимка — запись не выполняется (`SnapshotMissing`, код 1). Пакетная правка получает один `run_id` на все записи:
`publish rollback --run <run_id>` откатывает весь прогон. Эталонные снимки живых страниц — `data/snapshots/live/`.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .errors import EngineError, SnapshotMissing
from .store import SCHEMA_VERSION


ROLLBACK = "rollback-"


def new_run_id(instance, prefix: str = "run-") -> str:
    """Id прогона: время до секунды и номер, если в ту же секунду прогон уже был."""
    base = prefix + instance.now().strftime("%Y%m%d-%H%M%S") + f"-p{os.getpid()}"
    root = instance.root / "data" / "snapshots" / "before"
    taken = {p.name for p in root.glob("*/*") if p.is_dir()} if root.is_dir() else set()
    run_id, n = base, 2
    while run_id in taken:
        run_id, n = f"{base}-{n}", n + 1
    return run_id


def _folder(instance, tid: str, run_id: str) -> Path:
    for name, value in (("id темы", tid), ("id прогона", run_id)):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value or ""):
            raise EngineError(f"{name} «{value}»: латиница, цифры, «-» и «_»")
    return instance.root / "data" / "snapshots" / "before" / tid / run_id


def save(instance, tid: str, run_id: str, *, target: str, kind: str, read_from: str, exists: bool,
         content, meta: dict | None = None) -> Path:
    folder = _folder(instance, tid, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    n = len(list(folder.glob("*.json"))) + 1
    record = {"schema_version": SCHEMA_VERSION, "kind": "snapshot_before", "id": tid, "run_id": run_id,
              "n": n, "target": target, "action": kind, "read_from": read_from,
              "taken": instance.now().isoformat(timespec="seconds"), "exists": exists, "content": content,
              "meta": meta or {}}
    path = folder / f"{n:03d}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    back = json.loads(path.read_text(encoding="utf-8"))
    if back["content"] != content or back["target"] != target:
        raise EngineError(f"снимок «до» {path.name}: записан — прочитан другим")
    return path


def load(instance, *, tid: str | None = None, run_id: str | None = None) -> list[dict]:
    """Снимки темы (последний прогон) или прогона целиком (по всем темам)."""
    base = instance.root / "data" / "snapshots" / "before"
    if run_id:
        files = sorted(base.glob(f"*/{run_id}/*.json"))
    elif tid:
        # последний прогон публикации темы; снимки, снятые самим откатом, — не «до» публикации
        runs = sorted((p for p in (base / tid).glob("*") if p.is_dir() and not p.name.startswith(ROLLBACK)),
                      key=lambda p: p.name) if (base / tid).is_dir() else []
        files = sorted(runs[-1].glob("*.json")) if runs else []
    else:
        raise EngineError("нужен id темы или --run")
    out = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    if not out:
        what = f"прогона {run_id}" if run_id else f"темы {tid}"
        raise SnapshotMissing(f"снимков «до» у {what} нет — откатывать нечем")
    return out


def save_live(instance, slug: str, data: dict) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
        raise EngineError(f"slug «{slug}» для эталонного снимка: латиница, цифры, «-» и «_»")
    folder = instance.root / "data" / "snapshots" / "live"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug}.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "kind": "snapshot_live", **data},
                               ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return path
