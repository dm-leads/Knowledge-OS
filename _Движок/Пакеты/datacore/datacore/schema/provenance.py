"""Происхождение строки (Я2). К1: без системы, класса A–F, идентификатора загрузки и времени с поясом строка не записывается."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .errors import RuleViolation
from .number import SOURCE_CLASSES


@dataclass(frozen=True)
class Provenance:
    source_system: str
    source_class: str
    load_id: str
    loaded_at: datetime

    def __post_init__(self):
        for name in ("source_system", "load_id"):
            if not getattr(self, name):
                raise RuleViolation("К1", f"происхождение: пусто поле «{name}»")
        if self.source_class not in SOURCE_CLASSES:
            raise RuleViolation("К1", f"происхождение: класс источника «{self.source_class}» не из A–F")
        if not isinstance(self.loaded_at, datetime) or self.loaded_at.tzinfo is None:
            raise RuleViolation("К1", "происхождение: loaded_at обязан быть временем с поясом")

    def as_columns(self) -> dict:
        return {"source_system": self.source_system, "source_class": self.source_class,
                "load_id": self.load_id, "loaded_at": self.loaded_at}
