"""Яндекс.Вебмастер — популярные запросы сайта (Т4.2). Только чтение, OAuth-токен по имени переменной.

Разреза «страница × запрос» у API нет (проба В4, 25.10.2026): `query-analytics/list` отдаёт на адрес только один
самый частый запрос и только за две недели. Поэтому адаптер берёт запросы сайта целиком; страницу запроса дают
Search Console и Keys.so.
"""
from __future__ import annotations

from urllib.parse import quote

from ..core.number import FACT
from .base import ReadOnlyClient
from .demand import demand_row

API = "https://api.webmaster.yandex.net/v4"
PAGE = 500
MAX_PAGES = 40


class Webmaster:
    name = "webmaster"

    def __init__(self, user_id: str, host_id: str, token: str, as_of: str, *, transport=None):
        self.as_of = as_of
        self.endpoint = f"user/{user_id}/hosts/{quote(host_id, safe='')}/search-queries/popular"
        self.client = ReadOnlyClient("Вебмастер", API, {self.endpoint}, {"Authorization": f"OAuth {token}"},
                                     transport=transport)

    def fetch(self, period_from: str, period_to: str) -> list[dict]:
        rows = []
        for page in range(MAX_PAGES):
            params = {"order_by": "TOTAL_SHOWS",
                      "query_indicator": ["TOTAL_SHOWS", "TOTAL_CLICKS", "AVG_SHOW_POSITION"],
                      "device_type_indicator": "ALL", "date_from": period_from, "date_to": period_to,
                      "offset": page * PAGE, "limit": PAGE}
            data = self.client.call("GET", self.endpoint, params=params)
            chunk = data.get("queries") or []
            for q in chunk:
                ind = q.get("indicators") or {}
                rows.append(demand_row(q.get("query_text", ""), self.name, period_from, period_to, self.as_of,
                                       impressions=(ind.get("TOTAL_SHOWS") or 0, FACT),
                                       clicks=(ind.get("TOTAL_CLICKS") or 0, FACT),
                                       position=(ind.get("AVG_SHOW_POSITION"), FACT)))
            if len(chunk) < PAGE:
                break
        return rows

    def probe(self) -> str:
        data = self.client.call("GET", self.endpoint, params={"order_by": "TOTAL_SHOWS",
                                                              "query_indicator": "TOTAL_SHOWS", "limit": 1})
        return f"ответ получен, запросов {len(data.get('queries') or [])}"
