"""Инстанс проекта: каталог с профилем, реестрами, кэшем и отчётами. Всё, что знает о проекте команда, — отсюда."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import profile as profile_mod
from .errors import EngineError
from .normalize import Normalizer
from .env_file import secret
from .textindex import TextIndex

DATA = "data"
FILES = {
    "demand": "data/demand.jsonl", "clusters": "data/clusters.json", "demand_map": "data/demand_map.json",
    "reservations": "data/reservations.json", "queue": "data/queue.json", "rejections": "data/rejections.jsonl",
    "whitelist": "data/whitelist.json", "frozen": "data/frozen.json", "page_money": "data/page_money.csv",
    "known_answers": "known_answers.yaml",
}


class Instance:
    def __init__(self, profile_path, *, today: date | None = None, serp=None, transport=None):
        self.profile_path = Path(profile_path).resolve()
        self.root = self.profile_path.parent
        self.profile = profile_mod.load(self.profile_path)
        self.today = today or date.today()
        self.norm = Normalizer(self.profile["stop_words"])
        self.transport = transport          # подмена HTTP в тестах
        self._serp = serp
        self._corpus = None
        self._index = None

    @property
    def p(self) -> dict:
        return self.profile

    def file(self, name: str) -> Path:
        return self.root / FILES[name]

    def reports_dir(self, *parts) -> Path:
        path = self.root.joinpath("reports", self.today.isoformat(), *parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def corpus(self):
        """(страницы, отчёт) корпуса — читается один раз за запуск."""
        if self._corpus is None:
            from ..adapters.corpus import markdown_folder
            self._corpus = markdown_folder(self.profile, self.root)
        return self._corpus

    def index(self) -> TextIndex:
        if self._index is None:
            self._index = TextIndex(self.corpus()[0], self.norm)
        return self._index

    def serp(self):
        """Источник выдачи из профиля; в тестах — подменённый."""
        if self._serp is None:
            keysso = self.profile["sources"]["keysso"]
            if not keysso["enabled"]:
                raise EngineError("источник выдачи не подключён: sources.keysso.enabled = false")
            from ..adapters.keysso import KeyssoSerp
            self._serp = KeyssoSerp(secret(self.profile, keysso["key_env"]), transport=self.transport)
        return self._serp
