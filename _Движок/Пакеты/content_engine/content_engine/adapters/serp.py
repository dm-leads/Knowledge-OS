"""Интерфейс источника выдачи `SerpSource` (Т4.1).

Новый источник выдачи подключается новой реализацией этого интерфейса — ядро и шаги не правятся. Реализации:
`keysso` (рабочая) и `fake` (фикстура для тестов); обе проходят один набор тестов контракта.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

OK, NOT_IN_BASE = "ok", "нет в базе"


@dataclass
class SerpResult:
    phrase: str
    base: str
    status: str                                    # OK или NOT_IN_BASE
    top: list[tuple[str, int]] = field(default_factory=list)   # (адрес, позиция) по первым десяти

    def to_dict(self) -> dict:
        return {"phrase": self.phrase, "base": self.base, "status": self.status,
                "top": [[url, pos] for url, pos in self.top]}

    @classmethod
    def from_dict(cls, data: dict) -> "SerpResult":
        return cls(data["phrase"], data["base"], data["status"], [(u, int(p)) for u, p in data["top"]])


@dataclass
class SiteKeyword:
    url: str            # адрес страницы целиком
    query: str
    pos: int
    frequency: int      # точная частота
    bid: float | None   # цена клика в рекламе, если источник её знает


class SerpSource(Protocol):
    name: str

    def top(self, phrases: list[str], base: str) -> dict[str, SerpResult]:
        """Первые десять адресов выдачи по каждой фразе; фразы нет в базе источника — статус NOT_IN_BASE."""

    def site_keywords(self, domain: str, base: str, limit: int) -> list[SiteKeyword]:
        """Запросы домена в выдаче: какая страница, по какому запросу, на какой позиции."""

    def remaining(self) -> int:
        """Остаток суточной квоты в объектах."""
