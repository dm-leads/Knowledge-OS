"""Search Console — проверка адреса (URL Inspection, Т24.3). Только чтение: POST `urlInspection/index:inspect`.

В индексе — `inspectionResult.indexStatusResult.verdict == PASS` (проба 26.10.2026); рядом `coverageState` и
`lastCrawlTime`. Сервисный аккаунт и адрес сайта — из `sources.gsc`.
"""
from __future__ import annotations

from ..core.urls import ascii_url
from .base import ReadOnlyClient

API = "https://searchconsole.googleapis.com/v1"
ENDPOINT = "urlInspection/index:inspect"


class UrlInspection:
    def __init__(self, site_url: str, token: str, *, transport=None):
        self.site_url = site_url
        self.client = ReadOnlyClient("Search Console (проверка адреса)", API, {ENDPOINT},
                                     {"Authorization": f"Bearer {token}"}, transport=transport, retries=2)

    @classmethod
    def from_profile(cls, instance):
        from .gsc import service_account_token
        g = instance.p["sources"]["gsc"]
        return cls(g["site_url"], service_account_token(g["key_file"]), transport=instance.transport)

    def inspect(self, url: str) -> dict:
        data = self.client.call("POST", ENDPOINT, json={"inspectionUrl": ascii_url(url), "siteUrl": self.site_url})
        result = (data.get("inspectionResult") or {}).get("indexStatusResult") or {}
        return {"indexed": result.get("verdict") == "PASS", "verdict": result.get("verdict", ""),
                "coverage": result.get("coverageState", ""), "last_crawl": result.get("lastCrawlTime", "")}
