"""Деньги и заявки страницы за окно замера (модуль Д2, Т39.1): окно «день выкладки → +90 / +180» — та же лесенка
ценности Т4.4, что `adapters/value.py`, но с периодом на строку.

`bets.money.kind`:
- `file` — CSV `slug;money;leads;period_from;period_to;source;as_of` (разделитель «;»), несколько строк на страницу —
  разные окна; берётся строка с точным окном. Деньги есть — ступень 1 (факт); есть только заявки — ступень 2
  (заявки × `sources.value.average_check`, оценка);
- `module:<путь от инстанса>:<класс>` — адаптер проекта с методом `money(slug, period_from, period_to)` того же вида
  (например, вызывает выгрузку по странице входа с окном «с — по»);
- `none` — «не подключено».
Строки нет — ступень 3 (proxy: цена трафика Keys.so из уже снятой карты, без запросов) или 4 («нет данных»). **Ноль —
только из строки:** страницы нет в выгрузке — «нет данных», иначе неполная выгрузка дала бы ложный «провал».
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..core.errors import SourceError
from ..core.number import ESTIMATE, FACT, NO_DATA, PROXY, Number
from ..core.urls import slug_of

COLUMNS = ("slug", "money", "leads", "period_from", "period_to", "source", "as_of")
NOT_CONNECTED = "не подключено"


class FileWindows:
    def __init__(self, path: Path):
        if not path.is_file():
            raise SourceError(f"файл денег по окнам не найден: {path}")
        self.rows: dict[tuple[str, str, str], dict] = {}
        with path.open(encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            missing = set(COLUMNS) - set(reader.fieldnames or [])
            if missing:
                raise SourceError(f"{path.name}: нет колонок {sorted(missing)}")
            for r in reader:
                self.rows[(r["slug"].strip(), r["period_from"].strip(), r["period_to"].strip())] = r

    def money(self, slug: str, period_from: str, period_to: str) -> dict | None:
        return self.rows.get((slug, period_from, period_to))


class MoneyWindows:
    def __init__(self, instance):
        self.instance = instance
        cfg = instance.p["bets"]["money"]
        self.kind = cfg["kind"]
        self.source = None
        if self.kind == "file":
            path = Path(cfg["file"])
            self.source = FileWindows(path if path.is_absolute() else instance.root / path)
        elif self.kind.startswith("module:"):
            from ..core.plugins import load_class
            self.source = load_class(instance, self.kind, {}, "bets.money.kind")(instance)
        self.proxy = self._proxy_values()

    def _proxy_values(self) -> dict[str, float]:
        """Ступень 3 из уже собранной карты спроса — без запросов к источникам."""
        from ..core import store
        try:
            pages = store.read_json(self.instance.file("demand_map"), "demand_map", missing_ok=True)["records"]
        except Exception:
            return {}
        out = {}
        for pg in pages:
            v = (pg.get("value") or {})
            if v.get("step") == 3 and (v.get("money") or {}).get("value") is not None:
                out[slug_of(pg["url"]) or pg["url"]] = float(v["money"]["value"])
        return out

    def money(self, url: str, period_from: str, period_to: str) -> dict:
        """{"step": 1–4, "money": Number, "leads": Number | None, "connected": bool}."""
        slug = slug_of(url) or url
        as_of = self.instance.today.isoformat()
        if self.source is None:
            return {"step": 4, "connected": False, "leads": None,
                    "money": Number(None, period_from, period_to, "bets.money", NO_DATA, as_of, NOT_CONNECTED)}
        row = self.source.money(slug, period_from, period_to)
        if row:
            src, row_as_of = f"{self.kind.split(':')[0]}:{row['source']}", row["as_of"] or as_of
            leads = (Number(float(row["leads"]), period_from, period_to, src, FACT, row_as_of)
                     if str(row.get("leads") or "").strip() else None)
            if str(row.get("money") or "").strip():
                return {"step": 1, "connected": True, "leads": leads,
                        "money": Number(float(row["money"]), period_from, period_to, src, FACT, row_as_of, "ступень 1")}
            check = self.instance.p["sources"]["value"]["average_check"]
            if leads is not None and check:
                return {"step": 2, "connected": True, "leads": leads,
                        "money": Number(leads.value * check, period_from, period_to, src, ESTIMATE, row_as_of,
                                        "ступень 2: заявки × средний чек")}
        if slug in self.proxy:
            return {"step": 3, "connected": True, "leads": None,
                    "money": Number(self.proxy[slug], period_from, period_to, "keysso:avbid×wsk (карта спроса)", PROXY,
                                    as_of, "ступень 3: цена трафика")}
        return {"step": 4, "connected": True, "leads": None,
                "money": Number(None, period_from, period_to, "bets.money", NO_DATA, as_of,
                                "страницы нет в выгрузке за окно — нет данных, не ноль")}
