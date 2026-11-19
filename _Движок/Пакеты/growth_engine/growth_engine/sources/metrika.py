"""Адаптер веб-аналитики Яндекс Метрики (класс A): визиты и достижения целей-кнопок по страницам входа.

Нижняя ступень лестницы A — кнопка или форма на конкретной странице: разрез landing_page × цель.
Метрика видит только веб-поток. Визиты Метрики и сквозной аналитики — разные модели счёта: одно на другое не
делится (страж 4 по системе-источнику). Цели, переставшие срабатывать (Т5), отдаются как «нет данных».
"""
from __future__ import annotations

import re

import time
from datetime import date, timedelta

from ..core.config import InstanceConfig
from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, ReadOnlyClient, SourceError

SYSTEM = "metrika"
ENDPOINT = "stat/v1/data"
BREAKDOWN = "landing_page"
PARAM_PREFIX = "visit_param:"   # разрез по параметру визита (1.7.6): «visit_param:ab_h17» → сегменты «ab_h17=A», «ab_h17=B»
LEAD_VISITS = "lead_visits"     # визиты, в которых достигнута хотя бы одна цель-заявка из lead_goals конфигурации (1.7.6)
FLOW = "web"
LOCAL_FILE = "(локальный файл)"
EMPTY_PAGE = "(пусто)"


def _months(start: date, end: date):
    current = start
    while current < end:
        following = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
        yield current, min(following, end)
        current = following


class MetrikaAdapter:
    def __init__(self, section: dict, cfg: InstanceConfig, secrets: dict, transport=None, sleep=time.sleep):
        self.section, self.cfg = section, cfg
        token_env, counter_env = section["secret_env"]
        self.counter = secrets[counter_env]
        self.client = ReadOnlyClient(section["base_url"], section["allowed_endpoints"],
                                     {"Authorization": f"OAuth {secrets[token_env]}"},
                                     transport=transport, sleep=sleep)

    def _lead_filter(self, period_end: date) -> tuple[str | None, list[str]]:
        """Фильтр «в визите достигнута хотя бы одна цель-заявка». Список целей — в конфигурации (`lead_goals`, имена из
        `goals`): какие цели считать заявкой, решает инстанс, а не адаптер. Визит с двумя заявками считается один раз —
        это доля визитов, а не сумма достижений (сумма достижений бывает больше визитов и долей не является)."""
        names = list(self.section.get("lead_goals") or [])
        if not names:
            raise GuardViolation(9, "«lead_visits»: в конфигурации веб-аналитики нет lead_goals — какие цели считать заявкой")
        unknown = [name for name in names if name not in self.section["goals"]]
        if unknown:
            raise GuardViolation(9, f"«lead_visits»: цели {', '.join(unknown)} не описаны в goals")
        # Мёртвая цель исключается только для окон после даты остановки (как у самой цели-метрики): до неё она
        # срабатывала, и без неё история занижена (ревью 1.8.1, п. 3). Исключённые называются в пояснении числа.
        dead = self.section["dead_goals"]
        dead_now = set(dead["ids"]) if period_end > date.fromisoformat(dead["since"]) else set()
        ids = [int(self.section["goals"][name]) for name in names]
        live = [goal_id for goal_id in ids if goal_id not in dead_now]
        skipped = [name for name in names if int(self.section["goals"][name]) in dead_now]
        if not live:
            return None, skipped
        return "(" + " OR ".join(f"ym:s:goal{goal_id}IsReached=='Yes'" for goal_id in live) + ")", skipped

    def _param(self, breakdown: str) -> str | None:
        """Имя параметра визита из разреза «visit_param:<имя>»; имя — только из объявленных в конфигурации (страж 9)."""
        if not breakdown or not breakdown.startswith(PARAM_PREFIX):
            return None
        name = breakdown[len(PARAM_PREFIX):]
        if name not in (self.section.get("visit_params") or []):
            raise GuardViolation(9, f"параметр визита «{name}» не объявлен в visit_params конфигурации")
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise GuardViolation(9, f"параметр визита «{name}»: допустимы латиница, цифры и «_»")
        return name

    def _expression(self, metric: str) -> tuple[str, int | None]:
        if metric in ("site_visits", LEAD_VISITS):
            return "ym:s:visits", None
        name = metric.removeprefix("goal_")
        if metric.startswith("goal_") and name in self.section["goals"]:
            goal_id = int(self.section["goals"][name])
            return f"ym:s:goal{goal_id}reaches", goal_id
        raise GuardViolation(9, f"метрика «{metric}» не описана для веб-аналитики в конфигурации")

    def _segment(self, path: str) -> str:
        # Яндекс дописывает к адресу входа хвост «&lr=…&search_source=…&etext=…» — это не страница: варианты одной
        # страницы складываются, а непрозрачные токены хвоста не попадают в хранилище (1.2.3).
        path = re.split(r"[&?]|%3[DdFf]|%26", path, maxsplit=1)[0]   # и закодированные «=», «?», «&» (1.2.4)
        if not path:
            return f"{BREAKDOWN}={EMPTY_PAGE}"
        if any(path.lower().startswith(prefix.lower()) for prefix in self.section["junk_page_prefixes"]):
            return f"{BREAKDOWN}={LOCAL_FILE}"
        return f"{BREAKDOWN}={path}"

    def fetch(self, query: Query) -> list[Number]:
        if query.flow != FLOW:
            raise GuardViolation(9, "веб-аналитика видит только веб-поток — запрос с потоком web")
        if query.scope not in self.section["scopes"]:
            raise GuardViolation(9, f"кабинет «{query.scope}» не объявлен для веб-аналитики")
        param = self._param(query.breakdown)
        if query.breakdown not in (None, BREAKDOWN) and param is None:
            raise NotImplementedError(f"разрез «{query.breakdown}» пока не поддержан — только {BREAKDOWN} "
                                      f"и {PARAM_PREFIX}<параметр>")
        filters, skipped = [], []
        if query.metric == LEAD_VISITS:
            lead_filter, skipped = self._lead_filter(query.period_end)
            if lead_filter is None:
                common = dict(metric=query.metric, level=self.cfg.rule(query.metric).level, scope=query.scope,
                              flow=FLOW, period_start=query.period_start, period_end=query.period_end,
                              as_of=query.as_of, source=f"A:{SYSTEM}:{ENDPOINT}")
                return [Number(**common, status=Status.NO_DATA, value=None,
                               missing="все цели-заявки не срабатывают в этом окне (Т5) — «нет данных», а не 0")]
            filters.append(lead_filter)
        if param:
            filters.append(f"ym:s:paramsLevel1=='{param}'")
        expression, goal_id = self._expression(query.metric)
        common = dict(metric=query.metric, level=self.cfg.rule(query.metric).level, scope=query.scope, flow=FLOW,
                      period_start=query.period_start, period_end=query.period_end, as_of=query.as_of,
                      source=f"A:{SYSTEM}:{ENDPOINT}")
        dead = self.section["dead_goals"]
        if goal_id in dead["ids"] and query.period_end > date.fromisoformat(dead["since"]):
            return [Number(**common, status=Status.NO_DATA, value=None,
                           missing=f"цель не срабатывает с {dead['since']} (Т5) — «нет данных», а не 0")]
        total, by_segment, sampled, truncated = 0.0, {}, False, False
        limit = self.section["breakdown_limit"]
        for start, end in _months(query.period_start, query.period_end):
            params = {"ids": self.counter, "metrics": expression, "date1": start.isoformat(),
                      "date2": (end - timedelta(days=1)).isoformat(), "accuracy": self.section["accuracy"]}
            if query.breakdown:
                # Параметры визита — множество: измерение ключа рядом со значением, строки чужих ключей отбрасываются
                # (ревью 1.8.1, п. 1), иначе значение другого параметра того же визита попало бы в сегменты.
                params.update({"dimensions": "ym:s:paramsLevel1,ym:s:paramsLevel2" if param else "ym:s:startURLPath",
                               "limit": limit})
            if filters:
                params["filters"] = " AND ".join(filters)
            body = self.client.call("GET", ENDPOINT, params=params)
            sampled = sampled or bool(body.get("sampled"))
            if query.breakdown:
                rows = body.get("data") or []
                truncated = truncated or len(rows) >= limit
                for row in rows:
                    if param:
                        if str((row["dimensions"][0] or {}).get("name") or "") != param:
                            continue
                        name = str((row["dimensions"][1] or {}).get("name") or "")
                    else:
                        name = str((row["dimensions"][0] or {}).get("name") or "")
                    segment = f"{param}={name or EMPTY_PAGE}" if param else self._segment(name)
                    by_segment[segment] = by_segment.get(segment, 0.0) + float(row["metrics"][0])
            else:
                total += float((body.get("totals") or [0])[0])
        notes = [text for flag, text in ((sampled, "сэмплирование веб-аналитики"),
                                         (truncated, f"разрез обрезан на {limit} строках")) if flag]
        if skipped:
            notes.append(f"цели-заявки не срабатывают в окне и не учтены: {', '.join(skipped)}")
        status = Status.ESTIMATE if notes else Status.FACT
        missing = "; ".join(notes)
        if not query.breakdown:
            return [Number(**common, status=status, value=total, missing=missing)]
        return [Number(**common, segment=segment, status=status, value=value, missing=missing)
                for segment, value in sorted(by_segment.items())]

    def probe(self) -> list[ProbeResult]:
        yesterday = date.today() - timedelta(days=1)
        try:
            body = self.client.call("GET", ENDPOINT, params={
                "ids": self.counter, "metrics": "ym:s:visits", "date1": yesterday.isoformat(),
                "date2": yesterday.isoformat(), "accuracy": self.section["accuracy"]})
        except SourceError as exc:
            return [ProbeResult(SYSTEM, False, None, str(exc))]
        return [ProbeResult(SYSTEM, True, 200, f"визиты за {yesterday:%d.%m.%Y}: {(body.get('totals') or [0])[0]:g}")]
