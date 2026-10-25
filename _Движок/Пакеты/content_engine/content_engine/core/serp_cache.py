"""Кэш выдачи (Т7.5): файл на фразу × базу в `<инстанс>/cache/serp/<база>/`, живёт `serp_cache_days` дней."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path

from ..adapters.serp import SerpResult
from .store import SCHEMA_VERSION


class SerpCache:
    def __init__(self, root: Path, days: int, today: date):
        self.root = Path(root) / "cache" / "serp"
        self.days = days
        self.today = today

    def _path(self, phrase: str, base: str) -> Path:
        digest = hashlib.sha1(phrase.strip().lower().encode("utf-8")).hexdigest()[:20]
        return self.root / base / f"{digest}.json"

    def get(self, phrase: str, base: str, *, fresh_only: bool = False) -> SerpResult | None:
        """Свежая запись кэша или None. `fresh_only` — только снятая сегодня (взятие темы в работу)."""
        path = self._path(phrase, base)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema_version") != SCHEMA_VERSION:
                return None
            taken = date.fromisoformat(data["as_of"])
        except (ValueError, KeyError, AttributeError):
            return None                      # битая или чужая запись — перезапрашивается по правилам бюджета
        limit = self.today if fresh_only else self.today - timedelta(days=self.days)
        if taken < limit:
            return None
        return SerpResult.from_dict(data["result"])

    def as_of(self, phrase: str, base: str) -> str | None:
        path = self._path(phrase, base)
        return json.loads(path.read_text(encoding="utf-8"))["as_of"] if path.is_file() else None

    def put(self, result: SerpResult):
        path = self._path(result.phrase, result.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "as_of": self.today.isoformat(),
                                   "result": result.to_dict()}, ensure_ascii=False), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
