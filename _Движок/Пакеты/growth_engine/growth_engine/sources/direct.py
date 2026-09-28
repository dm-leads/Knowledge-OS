"""Адаптер рекламного кабинета Яндекс Директа (класс B): расход с НДС, клики и показы по кампаниям.

Только методы чтения — отчёты и список кампаний. Кабинет задаётся заголовком Client-Login из секретов:
MCP-сервер Директа отдаёт чужой кабинет и не используется. Отчёт в режиме auto может встать в очередь —
ответы 201/202 ожидаются по заголовку retryIn. Цена клика и CPUser считаются движком экономики, не здесь.
"""
from __future__ import annotations

import time
from datetime import timedelta

from ..core.config import InstanceConfig
from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, ReadOnlyClient, SourceError

SYSTEM = "direct"
REPORTS = "reports"
CAMPAIGNS = "campaigns"
BREAKDOWN = "campaign"
FIELDS = {"ad_spend": "Cost", "ad_clicks": "Clicks", "ad_impressions": "Impressions"}
REPORT_HEADERS = {"processingMode": "auto", "returnMoneyInMicros": "false",
                  "skipReportHeader": "true", "skipReportSummary": "true"}


def _number(cell: str) -> float:
    cell = cell.strip()
    return 0.0 if cell in ("", "--") else float(cell.replace(",", "."))


class DirectAdapter:
    def __init__(self, section: dict, cfg: InstanceConfig, secrets: dict, transport=None, sleep=time.sleep):
        self.section, self.cfg = section, cfg
        token_env, login_env = section["secret_env"]
        headers = {"Authorization": f"Bearer {secrets[token_env]}", "Client-Login": secrets[login_env],
                   "Accept-Language": "ru"}
        self.client = ReadOnlyClient(section["base_url"], section["allowed_endpoints"], headers,
                                     transport=transport, sleep=sleep)

    def fetch(self, query: Query) -> list[Number]:
        if query.metric not in FIELDS:
            raise GuardViolation(9, f"метрика «{query.metric}» из кабинета Директа не отдаётся")
        if query.flow != "all" or query.scope not in self.section["scopes"]:
            raise GuardViolation(9, "кабинет Директа: кабинет из конфигурации и поток all")
        if query.breakdown not in (None, BREAKDOWN):
            raise NotImplementedError(f"разрез «{query.breakdown}» пока не поддержан — только {BREAKDOWN}")
        field = FIELDS[query.metric]
        last_day = (query.period_end - timedelta(days=1)).isoformat()
        body = {"params": {
            "SelectionCriteria": {"DateFrom": query.period_start.isoformat(), "DateTo": last_day},
            "FieldNames": ["CampaignId", field],
            "ReportName": f"growth-engine {query.metric} {query.period_start} {last_day} {query.breakdown or 'total'}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT", "DateRangeType": "CUSTOM_DATE", "Format": "TSV",
            "IncludeVAT": "YES", "IncludeDiscount": "NO"}}
        text = self.client.call("POST", REPORTS, json=body, headers=REPORT_HEADERS, raw=True, wait_statuses=(201, 202))
        lines = [line for line in text.splitlines() if line.strip()]
        by_campaign = {}
        if lines:
            header = lines[0].split("\t")
            if "CampaignId" not in header or field not in header:
                raise SourceError(f"{REPORTS}: в отчёте нет колонок CampaignId и {field}")
            campaign_at, value_at = header.index("CampaignId"), header.index(field)
            for line in lines[1:]:
                cells = line.split("\t")
                by_campaign[cells[campaign_at]] = by_campaign.get(cells[campaign_at], 0.0) + _number(cells[value_at])
        money = query.metric == "ad_spend"
        common = dict(metric=query.metric, level=self.cfg.rule(query.metric).level, scope=query.scope, flow="all",
                      period_start=query.period_start, period_end=query.period_end, as_of=query.as_of,
                      source=f"B:{SYSTEM}:{REPORTS}",
                      unit=self.section["currency"] if money else "шт", status=Status.FACT)
        if query.breakdown is None:
            return [Number(**common, value=sum(by_campaign.values()))]
        return [Number(**common, segment=f"{BREAKDOWN}={campaign}", value=value)
                for campaign, value in sorted(by_campaign.items())]

    def probe(self) -> list[ProbeResult]:
        try:
            body = self.client.call("POST", CAMPAIGNS, json={"method": "get", "params": {
                "SelectionCriteria": {"States": ["ON"]}, "FieldNames": ["Id"]}})
        except SourceError as exc:
            return [ProbeResult(SYSTEM, False, None, str(exc))]
        if "error" in body:
            return [ProbeResult(SYSTEM, False, 200, f"ошибка API: {body['error'].get('error_code')}")]
        return [ProbeResult(SYSTEM, True, 200, f"активных кампаний: {len(body.get('result', {}).get('Campaigns', []))}")]
