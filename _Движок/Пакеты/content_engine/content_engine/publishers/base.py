"""Контракт публикатора (Т21.3): `plan(пакет) → действия`, снимок «до», применение, `read_back(адрес)`, откат.

Снимок и применение живут в самих действиях (`core/actions.py`: `Action.snapshot`, `Action.do`, `Action.verify`):
так ядро одним порядком делает снимки всех действий до первой записи и подтверждает каждую запись своим видом.
`restore(снимки)` — действия возврата тем же адаптером для `publish rollback`. Действие выше ступени прав в план не
попадает (`core/rights.split`). Публикатор проекта из инстанса (`module:<путь>:<класс>`) — тот же контракт.
"""
from __future__ import annotations

from typing import Protocol


class Publisher(Protocol):
    name: str

    def __init__(self, instance, *, transport=None): ...

    def plan(self, package, update_only: bool = False) -> list:
        """Все действия публикации пакета по ступеням (commit, publish); фильтр по правам — в ядре. `update_only` —
        правка страницы: запись цели, которой нет в момент записи, — ошибка, а не новая страница."""

    def read_back(self, url: str) -> tuple[int, str]:
        """Что сейчас на сайте по адресу: код ответа и текст."""

    def restore(self, snapshots: list[dict]) -> list:
        """Действия возврата состояния из снимков «до»."""


def load_publisher(instance, transport=None):
    """Публикатор профиля или None в ручном режиме без публикатора."""
    from ..core.plugins import load_class
    spec = instance.p["publish"]["adapter"]
    if spec == "none":
        return None
    from .cms_api import CmsApiPublisher
    from .static_repo import StaticRepoPublisher
    cls = load_class(instance, spec, {"static_repo": StaticRepoPublisher, "cms_api": CmsApiPublisher},
                     "publish.adapter")
    return cls(instance, transport=transport)
