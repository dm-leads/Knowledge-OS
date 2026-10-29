"""Реестры и журнал — файлы инстанса (Т3).

Каждый файл несёт `schema_version`; чужая версия — ошибка, а не молчаливое чтение. Запись — через временный файл и
замену; после записи файл читается обратно и сверяется число записей: «записано N — прочитано N». Ноль записанных
при непустом входе — ошибка. Журнал JSONL только дополняется.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .errors import StoreError

SCHEMA_VERSION = 1


def _dump(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _check_version(path: Path, version):
    if version != SCHEMA_VERSION:
        raise StoreError(f"{path.name}: версия схемы {version!r}, пакет понимает {SCHEMA_VERSION}")


# реестр, который строит команда машины, — подсказка следующего шага для нового проекта вместо голого «не найден»
PRODUCED_BY = {"demand.jsonl": "demand collect", "clusters.json": "cluster", "demand_map.json": "map build",
               "queue.json": "queue build"}


def _missing(what: str, path) -> str:
    step = PRODUCED_BY.get(Path(path).name)
    return f"{what}: {path}" + (f" — ещё не собран: сначала `content-engine {step}`" if step else "")


def write_json(path, kind: str, records: list, *, meta: dict | None = None, source_count: int | None = None) -> str:
    """Записать реестр и прочитать обратно. Вернуть строку сверки «записано N — прочитано N»."""
    path = Path(path)
    if source_count and not records:
        raise StoreError(f"{path.name}: на входе {source_count}, записать нечего — ноль записанных это ошибка")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "kind": kind, **(meta or {}), "records": records}
    _dump(path, json.dumps(payload, ensure_ascii=False, indent=1))
    back = read_json(path, kind)
    if len(back["records"]) != len(records):
        raise StoreError(f"{path.name}: записано {len(records)} — прочитано {len(back['records'])}")
    for name, value in (meta or {}).items():
        if isinstance(value, list) and len(back.get(name) or []) != len(value):
            raise StoreError(f"{path.name}, {name}: записано {len(value)} — прочитано {len(back.get(name) or [])}")
    return f"{path.name}: записано {len(records)} — прочитано {len(back['records'])}"


def read_json(path, kind: str, *, missing_ok: bool = False) -> dict:
    path = Path(path)
    if not path.is_file():
        if missing_ok:
            return {"schema_version": SCHEMA_VERSION, "kind": kind, "records": []}
        raise StoreError(_missing("реестр не найден", path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StoreError(f"{path.name}: JSON не читается — {exc}") from exc
    if not isinstance(data, dict):
        raise StoreError(f"{path.name}: верхний уровень — не объект")
    _check_version(path, data.get("schema_version"))
    if data.get("kind") != kind:
        raise StoreError(f"{path.name}: это «{data.get('kind')}», ожидался «{kind}»")
    if not isinstance(data.get("records"), list):
        raise StoreError(f"{path.name}: нет списка records")
    return data


def write_jsonl(path, rows: list[dict], *, source_count: int | None = None) -> str:
    """Пересобрать файл JSONL целиком (сырые запросы) и сверить чтением."""
    path = Path(path)
    if source_count and not rows:
        raise StoreError(f"{path.name}: на входе {source_count}, записать нечего — ноль записанных это ошибка")
    path.parent.mkdir(parents=True, exist_ok=True)
    _dump(path, "".join(json.dumps({"schema_version": SCHEMA_VERSION, **row}, ensure_ascii=False) + "\n"
                        for row in rows))
    back = read_jsonl(path)
    if len(back) != len(rows):
        raise StoreError(f"{path.name}: записано {len(rows)} — прочитано {len(back)}")
    return f"{path.name}: записано {len(rows)} — прочитано {len(back)}"


def read_jsonl(path, *, missing_ok: bool = False) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        if missing_ok:
            return []
        raise StoreError(_missing("файл не найден", path))
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StoreError(f"{path.name}, строка {number}: JSON не читается — {exc}") from exc
        if not isinstance(row, dict):
            raise StoreError(f"{path.name}, строка {number}: не объект")
        _check_version(path, row.get("schema_version"))
        rows.append(row)
    return rows


def append_jsonl(path, rows: list[dict]) -> str:
    """Дописать строки в журнал (только добавлением) и сверить: стало ровно на N больше."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    before = len(read_jsonl(path, missing_ok=True))
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps({"schema_version": SCHEMA_VERSION, **row}, ensure_ascii=False) + "\n")
    after = len(read_jsonl(path))
    if after - before != len(rows):
        raise StoreError(f"{path.name}: дописано {len(rows)} — прочитано {after - before}")
    return f"{path.name}: дописано {len(rows)} — прочитано {after - before}"
