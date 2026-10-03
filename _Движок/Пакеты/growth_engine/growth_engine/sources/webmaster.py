"""Адаптер Яндекс Вебмастера (класс E): показы, клики и средняя позиция сайта в поиске по запросам.

Показы и клики — итог по дневной истории или разрез по запросу (первые N по показам). Средняя позиция —
только в разрезе по запросу и не складывается. История запросов хранится с даты из конфигурации: период
раньше — «нет данных». Разрез по запросу хранится короче истории — скользящим окном около 12 недель и с задержкой в
несколько дней: число разреза несёт период, который покрыт на деле. Нижняя ступень «запрос × наша страница» —
отдельный отчёт, в v1 не подключён.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from urllib.parse import quote

from ..core.config import InstanceConfig
from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, ReadOnlyClient, SourceError

SYSTEM = "webmaster"
BREAKDOWN = "query"
INDICATORS = {"search_impressions": "TOTAL_SHOWS", "search_clicks": "TOTAL_CLICKS", "search_position": "AVG_SHOW_POSITION"}
HISTORY = "search-queries/all/history"
POPULAR = "search-queries/popular"


class WebmasterAdapter:
    def __init__(self, section: dict, cfg: InstanceConfig, secrets: dict, transport=None, sleep=time.sleep):
        self.section, self.cfg = section, cfg
        self.host = f"user/{section['user_id']}/hosts/{quote(section['host_id'], safe='')}"
        allowed = ["user"] + [f"{self.host}/{endpoint}" for endpoint in section["allowed_host_endpoints"]]
        self.client = ReadOnlyClient(section["base_url"], allowed,
                                     {"Authorization": f"OAuth {secrets[section['secret_env'][0]]}"},
                                     transport=transport, sleep=sleep)
        self._days: dict[date, bool] = {}              # есть ли у источника запросы за день — один вопрос на день

    def fetch(self, query: Query) -> list[Number]:
        if query.metric not in INDICATORS:
            raise GuardViolation(9, f"метрика «{query.metric}» из Вебмастера не отдаётся")
        if query.flow != "all" or query.scope not in self.section["scopes"]:
            raise GuardViolation(9, "Вебмастер: кабинет из конфигурации и поток all")
        if query.breakdown not in (None, BREAKDOWN):
            raise NotImplementedError(f"разрез «{query.breakdown}» пока не поддержан — только {BREAKDOWN}")
        indicator = INDICATORS[query.metric]
        if indicator == "AVG_SHOW_POSITION" and query.breakdown is None:
            raise GuardViolation(9, "средняя позиция без разреза по запросу не отдаётся")
        history_from = date.fromisoformat(self.section["history_from"])
        common = dict(metric=query.metric, level=self.cfg.rule(query.metric).level, scope=query.scope, flow="all",
                      period_start=query.period_start, period_end=query.period_end, as_of=query.as_of,
                      unit="позиция" if indicator == "AVG_SHOW_POSITION" else "шт")
        last_day = (query.period_end - timedelta(days=1)).isoformat()
        if query.period_start < history_from:
            return [Number(**common, source=f"E:{SYSTEM}:{HISTORY}", status=Status.NO_DATA, value=None,
                           missing=f"история запросов Вебмастера хранится с {history_from:%d.%m.%Y}")]
        if query.breakdown is None:
            body = self.client.call("GET", f"{self.host}/{HISTORY}", params=[
                ("query_indicator", indicator), ("date_from", query.period_start.isoformat()), ("date_to", last_day)])
            total = sum(float(point["value"]) for point in (body.get("indicators") or {}).get(indicator) or [])
            return [Number(**common, source=f"E:{SYSTEM}:{HISTORY}", status=Status.FACT, value=total)]
        rows, offset, top = [], 0, self.section["top_queries"]
        while offset < top:
            limit = min(self.section["page_size"], top - offset)
            body = self.client.call("GET", f"{self.host}/{POPULAR}", params=[
                ("order_by", "TOTAL_SHOWS"), ("query_indicator", "TOTAL_SHOWS"), ("query_indicator", indicator),
                ("date_from", query.period_start.isoformat()), ("date_to", last_day),
                ("limit", limit), ("offset", offset)])
            page = body.get("queries") or []
            rows += page
            offset += len(page)
            if len(page) < limit or offset >= int(body.get("count") or 0):
                break                                  # неполная страница — выдача закончилась
        note = f"первые {len(rows)} запросов по показам — остальные вне разреза"
        if rows:
            # Статистика по запросам лежит скользящим окном и выходит с задержкой (1.4.1): ответ за период молча
            # суммирует только хранимые дни. Число несёт период, который покрыт на деле, а не запрошенный.
            first, last = self._covered(query.period_start, query.period_end - timedelta(days=1))
            if (first, last) != (query.period_start, query.period_end - timedelta(days=1)):
                common.update(period_start=first, period_end=last + timedelta(days=1))
                note += (f"; период неполный: источник хранит запросы за {first:%d.%m.%Y}–{last:%d.%m.%Y}, запрошено "
                         f"{query.period_start:%d.%m.%Y}–{query.period_end - timedelta(days=1):%d.%m.%Y}")
        return [Number(**common, source=f"E:{SYSTEM}:{POPULAR}", segment=f"{BREAKDOWN}={item['query_text']}",
                       status=Status.FACT, value=float(item["indicators"][indicator]), missing=note)
                for item in rows if item.get("query_text")]

    def _has_queries(self, day: date) -> bool:
        if day not in self._days:
            body = self.client.call("GET", f"{self.host}/{POPULAR}", params=[
                ("order_by", "TOTAL_SHOWS"), ("query_indicator", "TOTAL_SHOWS"), ("date_from", day.isoformat()),
                ("date_to", day.isoformat()), ("limit", 1), ("offset", 0)])
            self._days[day] = int(body.get("count") or 0) > 0
        return self._days[day]

    def _covered(self, start: date, last: date) -> tuple[date, date]:
        """Первый и последний день периода, за которые источник отдаёт запросы. Хранимые дни идут подряд, поэтому край
        ищется делением пополам; если пусты оба края периода, сначала находится любой день с данными."""
        if self._has_queries(start):
            first = start
        else:
            anchor = last if self._has_queries(last) else next(
                (start + timedelta(days=i) for i in range(1, (last - start).days) if self._has_queries(start + timedelta(days=i))),
                None)
            if anchor is None:
                return start, last                       # ни одного дня с данными не видно — период не сужаем
            empty, first = start, anchor
            while (first - empty).days > 1:
                middle = empty + timedelta(days=(first - empty).days // 2)
                empty, first = (empty, middle) if self._has_queries(middle) else (middle, first)
        if self._has_queries(last):
            return first, last
        found, empty = first, last
        while (empty - found).days > 1:
            middle = found + timedelta(days=(empty - found).days // 2)
            found, empty = (middle, empty) if self._has_queries(middle) else (found, middle)
        return first, found

    def probe(self) -> list[ProbeResult]:
        try:
            body = self.client.call("GET", "user")
        except SourceError as exc:
            return [ProbeResult(SYSTEM, False, None, str(exc))]
        ok = str(body.get("user_id")) == str(self.section["user_id"])
        return [ProbeResult(SYSTEM, ok, 200, "user_id совпал" if ok else "user_id не совпал с конфигурацией")]
