"""Яндекс Метрика — отчёты API (`stat/v1/data`), только чтение (Т38). Токен и номер счётчика — по именам переменных
профиля (`behavior.token_env`, `counter_env`). Другие методы API (цели, счётчики, загрузки) клиенту недоступны.

Воронка по людям считается по каждому шагу отдельно: посетители, у которых в визитах есть страница шага (или
достигнута цель шага). Это не последовательная воронка Метрики — статус чисел «оценка».
"""
from __future__ import annotations

from .base import ReadOnlyClient

API = "https://api-metrika.yandex.net"
DATA = "stat/v1/data"
LIMIT = 1000


class Metrika:
    name = "metrika"

    def __init__(self, token: str, counter: str, *, transport=None):
        self.counter = counter
        self.client = ReadOnlyClient("Метрика", API, {DATA}, {"Authorization": f"OAuth {token}"},
                                     transport=transport)

    def _rows(self, frm: str, to: str, dimensions: str, metrics: str, filters: str = "") -> list[dict]:
        params = {"ids": self.counter, "date1": frm, "date2": to, "dimensions": dimensions, "metrics": metrics,
                  "limit": LIMIT, "accuracy": "full"}
        if filters:
            params["filters"] = filters
        data = self.client.call("GET", DATA, params=params)
        out = []
        for row in data.get("data") or []:
            out.append({"dims": [d.get("name") for d in row.get("dimensions") or []], "m": row.get("metrics") or []})
        return out

    def entry_pages(self, frm: str, to: str) -> list[dict]:
        """Страницы входа: посетители, визиты, отказы (доля 0–1), глубина, достижения целей."""
        rows = self._rows(frm, to, "ym:s:startURL", "ym:s:users,ym:s:visits,ym:s:bounceRate,ym:s:pageDepth,"
                                                     "ym:s:sumGoalReachesAny")
        return [{"url": r["dims"][0], "users": r["m"][0], "visits": r["m"][1], "bounce": r["m"][2] / 100,
                 "depth": r["m"][3], "goals": r["m"][4]} for r in rows if r["dims"] and r["dims"][0]]

    def by_device(self, frm: str, to: str) -> list[dict]:
        rows = self._rows(frm, to, "ym:s:startURL,ym:s:deviceCategory", "ym:s:users,ym:s:bounceRate")
        return [{"url": r["dims"][0], "device": r["dims"][1], "users": r["m"][0], "bounce": r["m"][1] / 100}
                for r in rows if len(r["dims"]) == 2 and r["dims"][0]]

    def step_users(self, frm: str, to: str, step: str) -> float | None:
        """Посетители шага: адрес — страница в визите; «goal:<id>» — достигшие цели."""
        if step.startswith("goal:"):
            rows = self._rows(frm, to, "", f"ym:s:goal{step[5:]}users")
        else:
            rows = self._rows(frm, to, "", "ym:s:users", filters=f"EXISTS(ym:pv:URL=@'{step}')")
        return float(rows[0]["m"][0]) if rows and rows[0]["m"] and rows[0]["m"][0] is not None else None
