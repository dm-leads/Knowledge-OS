"""Секреты по именам (Т2.3): файл `.env` из профиля, иначе из переменной `KOS_ENV`, иначе окружение процесса.

Значения не печатаются и не попадают в сообщения — только имена отсутствующих переменных. Переменная окружения
процесса главнее файла: так проверяют самопроверку с заведомо неверным ключом на один запуск.
"""
from __future__ import annotations

import os
from pathlib import Path

from .errors import SourceError


def _parse_env(path: Path) -> dict:
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[name] = value
    return values


def env_path(profile: dict) -> Path | None:
    configured = profile["secrets"]["env_file"] or os.environ.get("KOS_ENV", "")
    return Path(configured) if configured else None


def has_secret(profile: dict, name: str) -> bool:
    try:
        secret(profile, name)
    except SourceError:
        return False
    return True


def secret(profile: dict, name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    path = env_path(profile)
    if path and path.is_file():
        value = _parse_env(path).get(name)
        if value:
            return value
    where = f"в файле {path.name}" if path else "в окружении"
    raise SourceError(f"секрет «{name}» не найден {where}")
