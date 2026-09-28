"""Проба МойСклада (класс D): только проверка доступа через защищённый клиент moysklad_client.py.

В v1 деньги берутся из PnL-полей, сверенных с P&L, и МойСклад движком не читается. Прямые запросы к API
запрещены: токен админский и умеет писать, защита «только GET» живёт в клиенте — поэтому проба сначала
гоняет самотест этой защиты и без него запрос не отправляет.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from .base import ProbeResult

SYSTEM = "moysklad"


class MoySkladProbe:
    def __init__(self, section: dict, client_path=None):
        self.client_path = Path(client_path) if client_path else Path(section["path_root"]) / section["client"]

    def _load_client(self):
        spec = importlib.util.spec_from_file_location("moysklad_client", self.client_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def probe(self) -> list[ProbeResult]:
        try:
            client = self._load_client()
            if not client._selftest():
                return [ProbeResult(SYSTEM, False, None,
                                    "самотест защиты «только чтение» в клиенте не пройден — запрос не отправлен")]
            me = client.MoySklad().whoami()
        except (SystemExit, Exception) as exc:
            return [ProbeResult(SYSTEM, False, None, f"{type(exc).__name__}: {exc}")]
        ok = isinstance(me, dict) and bool(me.get("id"))
        return [ProbeResult(SYSTEM, ok, 200 if ok else None, "whoami (GET) ответил" if ok else "whoami без id")]
