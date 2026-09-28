"""Адаптер журнала событий amoCRM (класс D): сколько раз менялись выбранные поля сделок за период.

Нужен, чтобы отличать сезон от смены процесса в CRM по данным, а не по памяти людей. Наружу — только счётчики:
в значениях событий бывают ПДн (страж 12). Только чтение (страж 11): единственный эндпоинт — events.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

from ..core.config import InstanceConfig
from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, ReadOnlyClient, SourceError

SYSTEM = "amocrm_events"
ENDPOINT = "events"
BREAKDOWN = "field"
METRICS = ("crm_field_changes",)
MSK = timezone(timedelta(hours=3))


def _ts(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=MSK).timestamp())


class AmocrmEventsAdapter:
    def __init__(self, section: dict, cfg: InstanceConfig, secrets: dict, transport=None, sleep=time.sleep):
        self.section, self.cfg, self.sleep = section, cfg, sleep
        token_env, subdomain_env = section["secret_env"]
        base_url = section["base_url_template"].replace("{AMO_SUBDOMAIN}", secrets[subdomain_env])
        self.client = ReadOnlyClient(base_url, section["allowed_endpoints"],
                                     {"Authorization": f"Bearer {secrets[token_env]}"},
                                     transport=transport, sleep=sleep)

    def _count(self, event_type: str, start: date, end: date) -> tuple[int, bool]:
        """Число событий типа за период и признак «обрезано пределом страниц»."""
        count, page = 0, 1
        while page <= self.section["max_pages"]:
            body = self.client.call("GET", ENDPOINT, params={
                "limit": self.section["page_limit"], "page": page, "filter[type]": event_type,
                "filter[created_at][from]": _ts(start), "filter[created_at][to]": _ts(end) - 1})
            count += len((body.get("_embedded") or {}).get("events") or [])
            if "next" not in (body.get("_links") or {}):
                return count, False
            page += 1
            self.sleep(self.section["pause_seconds"])
        return count, True

    def fetch(self, query: Query) -> list[Number]:
        if query.metric not in METRICS:
            raise GuardViolation(9, f"метрика «{query.metric}» из журнала событий не отдаётся")
        if query.breakdown != BREAKDOWN:
            raise GuardViolation(9, f"журнал событий отдаётся только с разрезом «{BREAKDOWN}» — "
                                    "сумма изменений разных полей бессмысленна")
        if query.scope not in self.section["scopes"] or query.flow != "all":
            raise GuardViolation(9, "журнал событий: кабинет из конфигурации и поток all")
        level = self.cfg.rule(query.metric).level
        cap = self.section["max_pages"]
        rows = []
        for field_id in self.section["field_change_types"]:
            count, truncated = self._count(f"custom_field_{field_id}_value_changed",
                                           query.period_start, query.period_end)
            rows.append(Number(metric=query.metric, level=level, scope=query.scope, flow="all",
                               segment=f"{BREAKDOWN}={field_id}", period_start=query.period_start,
                               period_end=query.period_end, as_of=query.as_of, source=f"D:{SYSTEM}:{ENDPOINT}",
                               status=Status.ESTIMATE if truncated else Status.FACT, value=float(count),
                               missing=f"обрезано на {cap} страницах — событий больше" if truncated else ""))
        return rows

    def probe(self) -> list[ProbeResult]:
        try:
            body = self.client.call("GET", ENDPOINT, params={"limit": 1})
        except SourceError as exc:
            return [ProbeResult(SYSTEM, False, None, str(exc))]
        found = len((body.get("_embedded") or {}).get("events") or [])
        return [ProbeResult(SYSTEM, True, 200, f"событий на пробной странице: {found}")]
