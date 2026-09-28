"""Адаптер внешнего спроса (класс E): помесячная частотность фраз Wordstat через Yandex Search API.

Спрос и сезон дают потолок рынка и сезонную поправку к целям. Вызов платный: пакет больше порога из конфигурации
и любая проба требуют явного подтверждения. Незавершённый месяц отдаётся оценкой.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from ..core.config import InstanceConfig
from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, ReadOnlyClient, SourceError

SYSTEM = "yandex_search"
ENDPOINT = "wordstat/dynamics"
BREAKDOWN = "phrase"
METRICS = ("search_demand",)


class YandexSearchAdapter:
    def __init__(self, section: dict, cfg: InstanceConfig, secrets: dict, transport=None, sleep=time.sleep,
                 today=date.today, confirm_paid: bool = False):
        self.section, self.cfg, self.today, self.confirm_paid = section, cfg, today, confirm_paid
        key_env, folder_env = section["secret_env"]
        self.folder = secrets[folder_env]
        self.client = ReadOnlyClient(section["base_url"], section["allowed_endpoints"],
                                     {"Authorization": f"Api-Key {secrets[key_env]}"}, transport=transport, sleep=sleep)

    def fetch(self, query: Query) -> list[Number]:
        if query.metric not in METRICS:
            raise GuardViolation(9, f"метрика «{query.metric}» из внешнего спроса не отдаётся")
        if query.flow != "all":
            raise GuardViolation(9, "спрос не делится на потоки сайта — только all")
        if query.scope not in self.section["scopes"]:
            raise GuardViolation(9, f"регион «{query.scope}» не объявлен в конфигурации")
        if query.breakdown != BREAKDOWN:
            raise GuardViolation(9, "частотности разных фраз не складываются — только разрез phrase")
        phrases = list(self.section["demand_phrases"])
        if len(phrases) > self.section["confirm_batch_over"] and not self.confirm_paid:
            raise SourceError(f"платный пакет из {len(phrases)} запросов требует подтверждения")
        region = self.section["scopes"][query.scope]["region"]
        unfinished = query.period_end > self.today().replace(day=1)
        common = dict(metric=query.metric, level=self.cfg.rule(query.metric).level, scope=query.scope, flow="all",
                      period_start=query.period_start, period_end=query.period_end, as_of=query.as_of,
                      source=f"E:{SYSTEM}:{ENDPOINT}")
        rows = []
        for phrase in phrases:
            body = self.client.call("POST", ENDPOINT, json={
                "phrase": phrase, "regions": [region], "folderId": self.folder, "period": "PERIOD_MONTHLY",
                "fromDate": f"{query.period_start:%Y-%m-%d}T00:00:00Z",
                "toDate": f"{query.period_end - timedelta(days=1):%Y-%m-%d}T23:59:59Z"})
            total = sum(float(point["count"]) for point in body.get("results") or []
                        if query.period_start <= date.fromisoformat(point["date"][:10]) < query.period_end)
            rows.append(Number(**common, segment=f"{BREAKDOWN}={phrase}",
                               status=Status.ESTIMATE if unfinished else Status.FACT, value=total,
                               missing="месяц не завершён — частотность неполная" if unfinished else ""))
        return rows

    def probe(self) -> list[ProbeResult]:
        if not self.confirm_paid:
            return [ProbeResult(SYSTEM, False, None, "проба платная — не выполнена без подтверждения")]
        try:
            body = self.client.call("POST", ENDPOINT, json={
                "phrase": self.section["demand_phrases"][0], "regions": ["225"], "folderId": self.folder,
                "period": "PERIOD_MONTHLY", "fromDate": "2026-01-01T00:00:00Z", "toDate": "2026-01-31T23:59:59Z"})
        except SourceError as exc:
            return [ProbeResult(SYSTEM, False, None, str(exc))]
        return [ProbeResult(SYSTEM, True, 200, f"точек динамики: {len(body.get('results') or [])}")]
