"""Загрузка страниц (Т18.4, Т22.1): только GET, белый список, тайм-аут и предел размера, без исполнения скриптов.

Два режима белого списка: адреса (разбор топа — только адреса снимка выдачи дня взятия темы и отобранные адреса
доноров) и хосты (свой сайт — «доехало», внутренние ссылки, карта сайта). Адрес вне списка — `EndpointNotAllowed`,
запрос не отправлен. Ответ больше `prepare.page_max_kb` — отказ с причиной. Перенаправления по умолчанию не
выполняются: «доехало» требует 200 без перенаправлений, а 301 — это ответ, который надо назвать. Кэш —
`cache/pages/` на `prepare.page_cache_days` дней; кэш берётся только для разбора топа.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests

from ..core.errors import EndpointNotAllowed, SourceError
from ..core.store import SCHEMA_VERSION
from ..core.urls import host_of, host_unicode, norm_url

USER_AGENT = "Mozilla/5.0 (compatible; content-engine; +https://example.ru/bot)"


@dataclass
class Fetched:
    url: str
    status: int
    text: str = ""
    location: str = ""            # адрес перенаправления, если был
    from_cache: bool = False
    note: str = ""
    robots_header: str = ""      # X-Robots-Tag: запрет индексации бывает и заголовком ответа


class PageFetcher:
    def __init__(self, *, allowed_urls=None, allowed_hosts=None, timeout: int = 20, max_kb: int = 2048,
                 cache_dir: Path | None = None, cache_days: int = 0, today: date | None = None, transport=None):
        self.allowed_urls = {norm_url(u) for u in (allowed_urls or ())}
        self.allowed_hosts = {host_unicode(h) for h in (allowed_hosts or ())}
        self.timeout = timeout
        self.max_bytes = max_kb * 1024
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_days = cache_days
        self.today = today or date.today()
        if transport is None:
            session = requests.Session()
            session.trust_env = False
            transport = session.request
        self.transport = transport
        self.calls = 0

    @classmethod
    def for_own_site(cls, instance, transport=None):
        p = instance.p
        return cls(allowed_hosts=p["site"]["domains"], timeout=p["prepare"]["page_timeout"],
                   max_kb=p["prepare"]["page_max_kb"], today=instance.today,
                   transport=transport if transport is not None else instance.transport)

    def allowed(self, url: str) -> bool:
        normal = norm_url(url)
        return normal in self.allowed_urls or host_of(normal) in self.allowed_hosts

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha1(norm_url(url).encode("utf-8")).hexdigest()[:24] + ".json")

    def _cached(self, url: str) -> Fetched | None:
        path = self._cache_path(url)
        if not path or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            taken = date.fromisoformat(data["as_of"])
        except (ValueError, KeyError):
            return None
        if data.get("schema_version") != SCHEMA_VERSION or taken < self.today - timedelta(days=self.cache_days):
            return None
        return Fetched(data["url"], data["status"], data["text"], data.get("location", ""), True)

    def _store(self, page: Fetched):
        path = self._cache_path(page.url)
        if not path or page.status != 200:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "as_of": self.today.isoformat(), "url": page.url,
                                   "status": page.status, "text": page.text}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, url: str, *, allow_redirects: bool = False, use_cache: bool = False) -> Fetched:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        if not self.allowed(url):
            raise EndpointNotAllowed(f"загрузка страниц: адрес «{url}» вне белого списка — не загружен")
        if use_cache:
            hit = self._cached(url)
            if hit is not None:
                return hit
        self.calls += 1
        try:
            response = self.transport("GET", url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout,
                                      allow_redirects=allow_redirects, stream=True)
        except requests.exceptions.RequestException as exc:
            raise SourceError(f"{url}: сеть не отвечает — {type(exc).__name__}") from exc
        body = self._read(response, url)
        encoding = getattr(response, "encoding", None) or "utf-8"
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        headers = getattr(response, "headers", {}) or {}
        page = Fetched(url, response.status_code, text, headers.get("Location", ""),
                       robots_header=headers.get("X-Robots-Tag", ""))
        if use_cache:
            self._store(page)
        return page

    def _read(self, response, url: str) -> bytes:
        if hasattr(response, "iter_content"):
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError(f"{url}: ответ больше {self.max_bytes // 1024} КБ — не разобран")
                chunks.append(chunk)
            return b"".join(chunks)
        body = response.content if isinstance(response.content, bytes) else str(response.content).encode("utf-8")
        if len(body) > self.max_bytes:
            raise SourceError(f"{url}: ответ больше {self.max_bytes // 1024} КБ — не разобран")
        return body
