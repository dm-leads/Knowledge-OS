"""Карта источников инстанса: чтение из файла и лучший статус по классу A–F (шаг 0; стражи 12, 13).

Одна точка истины — файл инстанса; лист в хранилище — только выгрузка снимка.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .artifacts import SourceMapEntry
from .errors import GuardViolation

# Порядок статусов: подключённый источник важнее недоступного, недоступный — важнее отсутствующего.
_STATUS_RANK = {"подключён": 3, "есть, нет доступа": 2, "не применимо": 1, "нет": 0}


def parse_source_map(raw: dict) -> tuple[SourceMapEntry, ...]:
    entries, seen = [], set()
    for item in raw["sources"]:
        name = item["name"]
        if name in seen:
            raise GuardViolation(13, f"источник «{name}» описан в карте дважды")
        seen.add(name)
        entries.append(SourceMapEntry(
            name=name,
            source_class=item["class"],
            status=item["status"],
            history_from=str(item.get("history_from") or ""),
            truth_point=str(item.get("truth_point") or ""),
            probe=str(item.get("probe") or ""),
            traps=tuple(item.get("traps") or ()),
            secret_env_names=tuple(item.get("secret_env_names") or ()),
        ))
    return tuple(entries)


def load_source_map(path) -> tuple[SourceMapEntry, ...]:
    return parse_source_map(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def class_status(entries) -> dict:
    """Лучший статус источника по каждому классу. Класса без источников в словаре нет — это не «подключён»."""
    best = {}
    for entry in entries:
        current = best.get(entry.source_class)
        if current is None or _STATUS_RANK[entry.status] > _STATUS_RANK[current]:
            best[entry.source_class] = entry.status
    return best
