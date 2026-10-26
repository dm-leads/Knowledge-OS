"""Яндекс.Вебмастер: заявка на переобход и «адрес в поиске» (Т24.1, Т24.3). API v4, OAuth-токен по имени переменной.

Методы (проба 26.10.2026): квота — `GET …/recrawl/quota` (`daily_quota`, `quota_remainder`); заявка —
`POST …/recrawl/queue {url}` → `task_id`; статус заявки — `GET …/recrawl/queue/{task_id}` → `state`
(IN_PROGRESS, DONE, FAILED); адреса в поиске — `GET …/search-urls/in-search/samples` постранично. Суточный предел
переобхода считается до отправки, как квота Keys.so: остатка нет — заявка не отправляется. Подтверждение — id заявки
и её статус, прочитанный через API.
"""
from __future__ import annotations

from ..core.actions import Action
from ..core.env_file import secret
from ..core.errors import QuotaExhausted
from ..core.urls import ascii_url, norm_url
from .write_client import WriteClient

API = "https://api.webmaster.yandex.net/v4/user"
PAGE = 100


class WebmasterWrite:
    def __init__(self, instance, *, transport=None):
        w = instance.p["sources"]["webmaster"]
        base = f"{API}/{w['user_id']}/hosts/{w['host_id']}"
        allowed = {("GET", "recrawl/quota"), ("POST", "recrawl/queue"), ("GET", "recrawl/queue/*"),
                   ("GET", "search-urls/in-search/samples")}
        self.client = WriteClient("Вебмастер", base, allowed, {"Authorization": f"OAuth {secret(instance.p, w['token_env'])}"},
                                  hosts=["api.webmaster.yandex.net"],
                                  transport=transport if transport is not None else instance.transport)

    def quota(self) -> int:
        answer = self.client.call("GET", "recrawl/quota")
        return int((answer.data or {}).get("quota_remainder", 0))

    def recrawl_action(self, url: str) -> Action:
        full = ascii_url(url)
        state: dict = {}

        def do():
            if self.quota() < 1:
                raise QuotaExhausted("Вебмастер: суточный предел переобхода выбран — заявка не отправлена")
            answer = self.client.call("POST", "recrawl/queue", json={"url": full})
            state["task"] = (answer.data or {}).get("task_id")
            return answer

        def verify(answer):
            if answer.status not in (200, 201, 202) or not state.get("task"):
                return False, f"HTTP {answer.status}, id заявки нет"
            back = self.client.call("GET", f"recrawl/queue/{state['task']}")
            status = (back.data or {}).get("state", "")
            return status in ("IN_PROGRESS", "DONE"), f"заявка {state['task']}: {status or 'статус не прочитан'}"

        return Action("webmaster_recrawl", f"{self.client.base_url}/recrawl/queue", "POST", f"переобход {full}",
                      "ticket", ("indexing", "write"), do=do, verify=verify, needs_snapshot=False,
                      meta={"system": "webmaster", "url": url, "independent": True})

    def in_search(self, url: str, limit_pages: int = 50) -> bool | None:
        target = norm_url(url)
        offset = 0
        for _ in range(limit_pages):
            answer = self.client.call("GET", "search-urls/in-search/samples", params={"offset": offset, "limit": PAGE})
            data = answer.data or {}
            samples = data.get("samples") or []
            if any(norm_url(s.get("url", "")) == target for s in samples):
                return True
            offset += PAGE
            if not samples or offset >= int(data.get("count", 0)):
                return False
        return None
