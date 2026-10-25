"""Источник выдачи из фикстуры — для тестов и примеров. Проходит те же тесты контракта, что и рабочий адаптер.

Фикстура — JSON: {"remaining": N, "top": {"<база>": {"<фраза>": [[адрес, позиция], ...]}},
"site_keywords": {"<база>": [{"url", "query", "pos", "frequency", "bid"}]}}. Фразы нет — «нет в базе».
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.errors import QuotaExhausted
from ..core.urls import norm_url
from .serp import NOT_IN_BASE, OK, SerpResult, SiteKeyword


class FakeSerp:
    name = "fake"

    def __init__(self, data: dict | str | Path):
        if not isinstance(data, dict):
            data = json.loads(Path(data).read_text(encoding="utf-8"))
        self.data = data
        self.quota = int(data.get("remaining", 0))
        self.requested: list[str] = []

    def remaining(self) -> int:
        return self.quota

    def top(self, phrases: list[str], base: str) -> dict[str, SerpResult]:
        table = {k.lower(): v for k, v in self.data.get("top", {}).get(base, {}).items()}
        out = {}
        for phrase in phrases:
            places = table.get(phrase.strip().lower())
            if places is None:
                out[phrase] = SerpResult(phrase, base, NOT_IN_BASE)
                continue
            if self.quota <= 0:
                raise QuotaExhausted("fake: квота выбрана")
            self.quota -= 1
            self.requested.append(phrase)
            top = sorted(((norm_url(u), int(p)) for u, p in places if 1 <= int(p) <= 10), key=lambda x: x[1])
            out[phrase] = SerpResult(phrase, base, OK, top)
        return out

    def site_keywords(self, domain: str, base: str, limit: int) -> list[SiteKeyword]:
        rows = self.data.get("site_keywords", {}).get(base, [])
        return [SiteKeyword(norm_url(r["url"], domain), r["query"], int(r["pos"]), int(r["frequency"]),
                            r.get("bid")) for r in rows][:limit]

    def competitor_keywords(self, competitor: str, own_domain: str, base: str, limit: int) -> list[dict]:
        return list(self.data.get("competitor_keywords", {}).get(competitor, []))[:limit]
