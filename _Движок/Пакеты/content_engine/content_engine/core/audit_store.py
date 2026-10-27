"""Снимок аудита недели (модуль Д3, Т35): `data/audit/<дата>.json`. Пишет `audit run`, читают вердикт пульса (Т34.3),
план недели и следующий аудит (дельта к прошлому снимку, Т35.6).

Схема: `{"schema_version", "kind": "audit", "date", "scope": "full|changed", "pages", "findings": [{"id", "url",
"check", "level": "critical|high|medium|low", "detail", "first_seen"}]}`. id находки — устойчивый: «проверка|адрес»,
по нему считается дельта.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .errors import StoreError

LEVELS = ("critical", "high", "medium", "low")
SCHEMA_VERSION = 1


def folder(instance) -> Path:
    return instance.root / "data" / "audit"


def finding_id(check: str, url: str) -> str:
    return f"{check}|{url}"


def write(instance, scope: str, pages: int, findings: list[dict]) -> str:
    for f in findings:
        if f.get("level") not in LEVELS:
            raise StoreError(f"находка аудита {f.get('id')}: уровень «{f.get('level')}» не из {LEVELS}")
    path = folder(instance) / f"{instance.today.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": SCHEMA_VERSION, "kind": "audit", "date": instance.today.isoformat(), "scope": scope,
            "pages": pages, "findings": findings}
    text = json.dumps(data, ensure_ascii=False, indent=1)
    path.write_text(text, encoding="utf-8", newline="\n")
    back = json.loads(path.read_text(encoding="utf-8"))
    if len(back["findings"]) != len(findings):
        raise StoreError(f"{path.name}: записано находок {len(findings)} — прочитано {len(back['findings'])}")
    return f"{path.name}: записано находок {len(findings)} — прочитано {len(back['findings'])}"


def snapshots(instance) -> list[Path]:
    return sorted(folder(instance).glob("????-??-??.json")) if folder(instance).is_dir() else []


def latest(instance, *, before: str | None = None) -> dict | None:
    """Последний снимок (до даты `before`, если задана)."""
    paths = [p for p in snapshots(instance) if before is None or p.stem < before]
    if not paths:
        return None
    data = json.loads(paths[-1].read_text(encoding="utf-8"))
    if data.get("kind") != "audit" or data.get("schema_version") != SCHEMA_VERSION:
        raise StoreError(f"{paths[-1].name}: не снимок аудита версии {SCHEMA_VERSION}")
    return data


def age_days(instance, snap: dict | None) -> int | None:
    return None if snap is None else (instance.today - date.fromisoformat(snap["date"])).days
