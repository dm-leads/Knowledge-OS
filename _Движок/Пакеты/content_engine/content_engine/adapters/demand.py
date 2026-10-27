"""Источники спроса (`DemandSource`, Т4.2): `fetch(period_from, period_to) → строки запросов`.

Строка — запрос, страница (если источник её знает) и числа: показы, клики, позиция, частота. Каждое число несёт
период, источник, статус и дату съёма (Т3.3): показы и клики консолей — факт, позиции и частоты Keys.so — оценка
на месяц обновления базы.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol

from ..core.errors import SourceError
from ..core.number import ESTIMATE, FACT, Number

METRICS = ("impressions", "clicks", "position", "frequency", "asks")   # asks — обращения клиентов (Т31.1)


class DemandSource(Protocol):
    name: str

    def fetch(self, period_from: str, period_to: str) -> list[dict]:
        """Строки спроса за период (даты ISO, включительно)."""


def demand_row(query: str, source: str, period_from: str, period_to: str, as_of: str, *, page: str = "",
               **values) -> dict:
    """Строка спроса: значения — пары (число, статус) или None."""
    numbers = {}
    for metric in METRICS:
        pair = values.get(metric)
        if pair is None or pair[0] is None:
            continue
        value, status = pair
        numbers[metric] = Number(value, period_from, period_to, f"{source}:{metric}", status, as_of).to_dict()
    return {"query": query.strip(), "page": page, "source": source, "numbers": numbers}


def value_of(row: dict, metric: str, default=0.0):
    item = row["numbers"].get(metric)
    return item["value"] if item and item["value"] is not None else default


class FileDemand:
    """Своё ядро запросов CSV: `query;impressions;frequency;source` (разделитель «;»)."""
    name = "demand_file"

    def __init__(self, path: Path, as_of: str):
        self.path = Path(path)
        self.as_of = as_of

    def fetch(self, period_from: str, period_to: str) -> list[dict]:
        if not self.path.is_file():
            raise SourceError(f"файл ядра не найден: {self.path}")
        rows = []
        with self.path.open(encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            missing = {"query", "impressions", "frequency", "source"} - set(reader.fieldnames or [])
            if missing:
                raise SourceError(f"{self.path.name}: нет колонок {sorted(missing)}")
            for r in reader:
                if not (r["query"] or "").strip():
                    continue
                imp = _num(r["impressions"])
                freq = _num(r["frequency"])
                rows.append(demand_row(r["query"], f"file:{r['source'] or self.path.name}", period_from, period_to,
                                       self.as_of, impressions=(imp, FACT) if imp is not None else None,
                                       frequency=(freq, ESTIMATE) if freq is not None else None))
        return rows


def _num(text):
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
