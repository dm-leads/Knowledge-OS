"""Общая часть адаптеров источников (слой 2): запрос без значений по умолчанию, белый список эндпоинтов,
отказ доступа отдельно от пустого ответа.

Адаптеры знают продукт источника и имена его полей — из раздела sources конфигурации инстанса; ядро их не знает.
Стражи: 9 (нет молчаливых значений по умолчанию), 11 (запрос вне белого списка не отправляется),
12 (секреты берутся по имени, значения в сообщения не попадают).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests
from dotenv import dotenv_values


class SourceError(RuntimeError):
    """Источник ответил ошибкой — это не «данных нет»."""


class AccessDenied(SourceError):
    """HTTP 401/403: доступ отозван или токен истёк."""


class EndpointNotAllowed(SourceError):
    """Эндпоинт вне белого списка адаптера: запрос не отправлен (страж 11)."""


@dataclass(frozen=True)
class Query:
    metric: str
    scope: str
    flow: str
    period_start: date
    period_end: date          # граница исключающая
    breakdown: str | None     # передаётся явно; None — без разреза
    as_of: date               # дата съёма: прогон ставит одну дату на все запросы (П3)


@dataclass(frozen=True)
class ProbeResult:
    source: str
    ok: bool
    http_status: int | None
    detail: str


def load_secrets(env_path, names) -> dict:
    """Значения секретов по именам. В сообщении об ошибке — только имена отсутствующих переменных."""
    values = dotenv_values(Path(env_path))
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise SourceError(f"в файле секретов нет переменных: {', '.join(missing)}")
    return {name: values[name] for name in names}


class ReadOnlyClient:
    """HTTP-клиент адаптера: только эндпоинты из белого списка, без прокси окружения; отказ доступа — исключение."""

    def __init__(self, base_url, allowed_endpoints, headers, transport=None, sleep=time.sleep, retries=3):
        self.base_url = base_url.rstrip("/")
        self.allowed = frozenset(allowed_endpoints)
        self.headers = dict(headers)
        self.sleep = sleep
        self.retries = retries
        if transport is None:
            session = requests.Session()
            session.trust_env = False          # прокси окружения рвёт TLS корпоративных доменов
            transport = session.request
        self.transport = transport

    def call(self, method, endpoint, *, params=None, json=None, headers=None, raw=False, wait_statuses=(),
             max_waits=20):
        """Ответ источника: JSON или текст (raw). wait_statuses — «отчёт готовится», ждать по заголовку retryIn."""
        if endpoint not in self.allowed:
            raise EndpointNotAllowed(f"эндпоинт «{endpoint}» не в белом списке адаптера — запрос не отправлен")
        url = f"{self.base_url}/{endpoint}"
        merged = {**self.headers, **(headers or {})}
        last, attempt, waits = "ответ не получен", 0, 0
        while attempt < self.retries:
            try:
                response = self.transport(method, url, params=params, json=json, headers=merged, timeout=180)
            except requests.exceptions.RequestException as exc:
                last = f"сеть не отвечает — {type(exc).__name__}"
            else:
                status = response.status_code
                if status in wait_statuses and waits < max_waits:
                    waits += 1
                    self.sleep(float((getattr(response, "headers", None) or {}).get("retryIn", 5)))
                    continue
                if status in (401, 403):
                    raise AccessDenied(f"{endpoint}: HTTP {status} — доступ отозван или токен истёк")
                if status == 204:
                    return "" if raw else {}         # «нет содержимого» — законно пустой ответ, не ошибка
                if status == 200:
                    return response.text if raw else response.json()
                if status != 429 and status < 500:
                    raise SourceError(f"{endpoint}: HTTP {status}")
                last = f"HTTP {status}"
            attempt += 1
            if attempt < self.retries:
                self.sleep(2 ** (attempt - 1))
        raise SourceError(f"{endpoint}: {last} после {self.retries} попыток")
