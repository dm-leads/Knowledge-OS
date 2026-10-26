"""Клиент записи (Т16.2): белый список пар «метод, адрес» и хостов, счётчик запросов записи.

Отдельный модуль: у клиента чтения (`adapters/base.py`) методов записи нет и не будет. Запрос вне списка не
отправляется — `EndpointNotAllowed`. Запись не повторяется сама: повтор мог бы создать вторую запись, поэтому обрыв
сети — ошибка, а решение о повторе — за человеком после чтения обратно. Секреты — в заголовках по именам переменных
профиля, в сообщения не попадают.
"""
from __future__ import annotations

import fnmatch
from urllib.parse import urlsplit

import requests

from ..core.errors import AccessDenied, EndpointNotAllowed, SourceError
from ..core.urls import host_unicode

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class Response:
    """Ответ без тела секрета: код, JSON или текст."""

    def __init__(self, status: int, data=None, text: str = ""):
        self.status = status
        self.data = data
        self.text = text


class WriteClient:
    def __init__(self, name: str, base_url: str, allowed: set[tuple[str, str]], headers: dict | None = None, *,
                 hosts=None, transport=None, timeout: int = 30):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.allowed = frozenset((m.upper(), e) for m, e in allowed)
        self.headers = dict(headers or {})
        self.hosts = {host_unicode(h) for h in hosts} if hosts is not None else None
        self.timeout = timeout
        if transport is None:
            session = requests.Session()
            session.trust_env = False
            transport = session.request
        self.transport = transport
        self.calls = 0                      # всех отправленных запросов
        self.writes = 0                     # отправленных запросов записи — «без --apply ноль»

    def _allowed(self, method: str, endpoint: str) -> bool:
        return any(m == method and fnmatch.fnmatchcase(endpoint, pattern) for m, pattern in self.allowed)

    def call(self, method: str, endpoint: str, *, params=None, json=None, data=None) -> Response:
        method = method.upper()
        host = host_unicode(urlsplit(self.base_url).hostname or "")
        if self.hosts is not None and host not in self.hosts:
            raise EndpointNotAllowed(f"{self.name}: хост «{host}» вне белого списка — запрос не отправлен")
        if not self._allowed(method, endpoint):
            raise EndpointNotAllowed(f"{self.name}: {method} «{endpoint}» не в белом списке — запрос не отправлен")
        url = f"{self.base_url}/{endpoint}" if endpoint else self.base_url
        self.calls += 1
        if method in WRITE_METHODS:
            self.writes += 1
        try:
            response = self.transport(method, url, params=params, json=json, data=data, headers=self.headers,
                                      timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise SourceError(f"{self.name} {method} {endpoint}: сеть не отвечает — {type(exc).__name__}") from exc
        status = response.status_code
        if status in (401, 403):
            raise AccessDenied(f"{self.name}: HTTP {status} — доступ отозван или ключ сменили")
        try:
            body = response.json() if response.text else None
        except ValueError:
            body = None
        return Response(status, body, response.text or "")
