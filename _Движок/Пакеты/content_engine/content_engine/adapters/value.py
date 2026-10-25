"""Ценность страницы (`PageValueSource`, Т4.4) — лесенка: берётся самая сильная доступная ступень.

1. деньги по странице — файл (CRM, Ядро данных или своя аналитика проекта) — факт;
2. заявки по странице — тот же файл; деньги = заявки × средний чек из профиля — заявки факт, деньги оценка;
3. цена трафика — Keys.so: цена клика (`avbid`) × точная частота (`wsk`) по запросам страницы — proxy: рынок платит за
   запрос, значит за ним есть деньги;
4. ничего нет — «нет данных».
Слой 4 гейта блокирует тему только по ступеням 1–2: proxy и отсутствие данных тему не запрещают.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from ..core.errors import SourceError
from ..core.number import ESTIMATE, FACT, NO_DATA, PROXY, Number
from ..core.urls import slug_of

COLUMNS = ("slug", "money", "leads", "period_from", "period_to", "source", "as_of")


class PageValue:
    def __init__(self, profile: dict, root: Path, today: date, *, site_keywords=None):
        self.cfg = profile["sources"]["value"]
        self.days = profile["thresholds"]["money_days"]
        self.today = today
        self.rows: dict[str, dict] = {}
        self.site_keywords = site_keywords or []      # [SiteKeyword] для ступени 3
        if self.cfg["kind"] == "file":
            path = Path(self.cfg["file"])
            path = path if path.is_absolute() else root / path
            if not path.is_file():
                raise SourceError(f"файл ценности страниц не найден: {path}")
            with path.open(encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                missing = set(COLUMNS) - set(reader.fieldnames or [])
                if missing:
                    raise SourceError(f"{path.name}: нет колонок {sorted(missing)}")
                for r in reader:
                    self.rows[r["slug"].strip()] = r

    def value(self, url: str) -> dict:
        """{"step": 1–4, "money": Number, "leads": Number | None}."""
        slug = slug_of(url) or url
        period_from = (self.today - timedelta(days=self.days)).isoformat()
        period_to = self.today.isoformat()
        row = self.rows.get(slug)
        if row:
            pf, pt, src, as_of = row["period_from"], row["period_to"], f"file:{row['source']}", row["as_of"]
            leads = Number(float(row["leads"]), pf, pt, src, FACT, as_of) if row["leads"].strip() else None
            if row["money"].strip():
                return {"step": 1, "money": Number(float(row["money"]), pf, pt, src, FACT, as_of, "ступень 1"),
                        "leads": leads}
            check = self.cfg["average_check"]
            if leads is not None and check:
                return {"step": 2, "money": Number(leads.value * check, pf, pt, src, ESTIMATE, as_of,
                                                   "ступень 2: заявки × средний чек"), "leads": leads}
        own = [k for k in self.site_keywords if slug_of(k.url) == slug and k.bid is not None]
        if own:
            proxy = sum(k.bid * k.frequency for k in own)
            return {"step": 3, "money": Number(proxy, period_from, period_to, "keysso:avbid×wsk", PROXY,
                                               self.today.isoformat(), "ступень 3: цена трафика"), "leads": None}
        return {"step": 4, "money": Number(None, period_from, period_to, "лесенка ценности", NO_DATA,
                                           self.today.isoformat(), "ступень 4: данных нет"), "leads": None}
