"""Google Search Console — показы, клики и позиция по запросам и по паре «страница × запрос» (Т4.2). Только чтение.

Сервисный аккаунт: путь к JSON-ключу — в профиле, область — `webmasters.readonly`. Запрос `searchAnalytics/query` —
это чтение, хоть и методом POST; адрес — в белом списке адаптера.
"""
from __future__ import annotations

from urllib.parse import quote

from ..core.number import FACT
from .base import ReadOnlyClient
from .demand import demand_row

API = "https://searchconsole.googleapis.com/webmasters/v3"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
ROW_LIMIT = 25000


def service_account_token(key_file: str) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    credentials = service_account.Credentials.from_service_account_file(key_file, scopes=[SCOPE])
    credentials.refresh(Request())
    return credentials.token


class SearchConsole:
    name = "gsc"

    def __init__(self, site_url: str, token: str, as_of: str, *, transport=None, sleep=None):
        self.site_url = site_url
        self.as_of = as_of
        self.endpoint = f"sites/{quote(site_url, safe='')}/searchAnalytics/query"
        kwargs = {"transport": transport}
        if sleep:
            kwargs["sleep"] = sleep
        self.client = ReadOnlyClient("Search Console", API, {self.endpoint},
                                     {"Authorization": f"Bearer {token}"}, **kwargs)

    def _rows(self, period_from, period_to, dimensions):
        out, start = [], 0
        while True:
            body = {"startDate": period_from, "endDate": period_to, "dimensions": dimensions, "type": "web",
                    "rowLimit": ROW_LIMIT, "startRow": start}
            data = self.client.call("POST", self.endpoint, json=body)
            chunk = data.get("rows") or []
            out.extend(chunk)
            if len(chunk) < ROW_LIMIT:
                return out
            start += ROW_LIMIT

    def fetch(self, period_from: str, period_to: str) -> list[dict]:
        """Запросы сайта целиком (разрез «запрос»)."""
        return [self._row(r["keys"][0], "", r, period_from, period_to)
                for r in self._rows(period_from, period_to, ["query"])]

    def fetch_pages(self, period_from: str, period_to: str) -> list[dict]:
        """Разрез «страница × запрос» — для карты спроса."""
        return [self._row(r["keys"][1], r["keys"][0], r, period_from, period_to)
                for r in self._rows(period_from, period_to, ["page", "query"])]

    def _row(self, query, page, r, period_from, period_to):
        return demand_row(query, self.name, period_from, period_to, self.as_of, page=page,
                          impressions=(r.get("impressions", 0), FACT), clicks=(r.get("clicks", 0), FACT),
                          position=(r.get("position"), FACT))

    def probe(self) -> str:
        data = self.client.call("POST", self.endpoint, json={"startDate": self.as_of, "endDate": self.as_of,
                                                             "dimensions": ["query"], "rowLimit": 1})
        return f"ответ получен, строк {len(data.get('rows') or [])}"
