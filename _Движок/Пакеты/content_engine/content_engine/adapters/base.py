"""Общий клиент адаптеров (Т4): только чтение, белый список адресов, отказ доступа отдельно от пустого ответа.

- запрос вне белого списка адаптера не отправляется;
- 401/403 — `AccessDenied`, а не «пусто»;
- повтор только на временных ошибках: тайм-аут, обрыв сети, 5xx;
- 429 решает адаптер (`on_429`): исчерпана квота — `QuotaExhausted` без повтора, частота — одна пауза и повтор;
- ответ HTTP 202 или `code: 202` при HTTP 200 — «отчёт готовится»: ждём, но не дольше `max_wait` секунд.
Методов записи у клиента нет: он отправляет только GET и POST-запросы чтения из белого списка.
"""
from __future__ import annotations

import time
from typing import Callable

import requests

from ..core.errors import AccessDenied, EndpointNotAllowed, NotFound, QuotaExhausted, SourceError

RETRY, STOP = "retry", "stop"


class ReadOnlyClient:
    def __init__(self, name: str, base_url: str, allowed: set[str], headers: dict, *, transport=None,
                 sleep: Callable[[float], None] = time.sleep, retries: int = 3, timeout: int = 120,
                 on_429: Callable[[], str] | None = None, max_wait: int = 360, wait_step: int = 15):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.allowed = frozenset(allowed)
        self.headers = dict(headers)
        self.sleep = sleep
        self.retries = retries
        self.timeout = timeout
        self.on_429 = on_429
        self.max_wait = max_wait
        self.wait_step = wait_step
        if transport is None:
            session = requests.Session()
            session.trust_env = False        # прокси окружения ломает TLS части доменов; адаптеры ходят напрямую
            transport = session.request
        self.transport = transport
        self.calls = 0                       # отправленных запросов — для тестов «ноль повторов»

    def call(self, method: str, endpoint: str, *, params=None, json=None):
        if endpoint not in self.allowed:
            raise EndpointNotAllowed(f"{self.name}: адрес «{endpoint}» не в белом списке — запрос не отправлен")
        if method not in ("GET", "POST"):
            raise EndpointNotAllowed(f"{self.name}: метод {method} не разрешён")
        url = f"{self.base_url}/{endpoint}"
        attempt, waited, rate_retried, last = 0, 0, False, "ответ не получен"
        while attempt < self.retries:
            try:
                self.calls += 1
                response = self.transport(method, url, params=params, json=json, headers=self.headers,
                                          timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                last = f"сеть не отвечает — {type(exc).__name__}"
            else:
                status = response.status_code
                if status in (401, 403):
                    raise AccessDenied(f"{self.name}: HTTP {status} — доступ отозван или ключ сменили")
                if status == 429:
                    decision = self.on_429() if self.on_429 else STOP
                    if decision == RETRY and not rate_retried:
                        rate_retried = True
                        self.sleep(60)
                        continue
                    raise QuotaExhausted(f"{self.name}: HTTP 429 — лимит исчерпан, повтор после обновления лимита")
                if status == 204:
                    return {}
                if status in (200, 202):
                    try:
                        data = response.json() if status == 200 else {"code": 202}
                    except ValueError as exc:
                        raise SourceError(f"{self.name} {endpoint}: ответ не JSON — {exc}") from exc
                    if isinstance(data, dict) and data.get("code") == 202:
                        if waited >= self.max_wait:
                            raise SourceError(f"{self.name}: отчёт не готов за {self.max_wait} с")
                        self.sleep(self.wait_step)
                        waited += self.wait_step
                        continue
                    return data
                if status == 404:
                    raise NotFound(f"{self.name} {endpoint}: HTTP 404")
                if status < 500:
                    raise SourceError(f"{self.name} {endpoint}: HTTP {status}")
                last = f"HTTP {status}"
            attempt += 1
            if attempt < self.retries:
                self.sleep(2 ** attempt)
        raise SourceError(f"{self.name} {endpoint}: {last} после {self.retries} попыток")
