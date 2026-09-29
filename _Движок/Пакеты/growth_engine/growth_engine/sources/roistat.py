"""Адаптер сквозной аналитики Roistat (класс C): визиты, заявки, MQL, New First SQL, продажи и PnL-поля
по кабинету и потоку.

Имена метрик, кабинеты, маркеры потоков и денежные метрики — только из раздела sources.roistat конфигурации.
Правая граница периода исключающая; фильтры по полям сделки не применяются — они режут визиты.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from ..core.config import InstanceConfig
from ..core.errors import GuardViolation
from ..core.number import Number, Status
from .base import ProbeResult, Query, ReadOnlyClient, SourceError

SYSTEM = "roistat"
ENDPOINT = "project/analytics/data"
DIMENSION = "marker_level_1"
# Второй уровень маркера: у поиска — система («seo › google», «seo › yandex»), у рефералов — сайт-донор. Нужен дереву
# цели: спад поиска год к году целиком в Google (28.09.2026), и одной веткой «seo» его не увидеть. Значение сегмента
# называет оба уровня: второй уровень без первого неоднозначен.
DIMENSION_2 = "marker_level_2"
LEVEL_SEPARATOR = " › "
# Страница входа (1.3.0): «маркер › путь». New First SQL по странице — честная мера страницы: цели веб-аналитики видят
# только формы, а мессенджеры, чат и звонки колл-трекинга — нет. Домен из адреса отрезается, главная — «/».
DIMENSION_PAGE = "landing_page"
EMPTY_MARKER = "(пусто)"   # у прямых визитов маркер пустой, а сегмент обязан иметь значение


class RoistatAdapter:
    def __init__(self, section: dict, cfg: InstanceConfig, secrets: dict, transport=None, sleep=time.sleep):
        self.section = section
        self.cfg = cfg
        headers = {"Api-key": secrets[section["secret_env"][0]], "Content-Type": "application/json"}
        self.client = ReadOnlyClient(section["base_url"], section["allowed_endpoints"], headers,
                                     transport=transport, sleep=sleep)

    def _rows(self, scope: str, names, start: date, end: date, depth: int = 1, second_dimension: str = DIMENSION_2):
        """Строки разреза: (маркер первого уровня, второе измерение или "", значения метрик)."""
        tz = self.section["timezone"]
        dimensions = [DIMENSION, second_dimension][:depth]
        payload = {
            "dimensions": dimensions,
            "metrics": list(names),
            "period": {"from": f"{start:%Y-%m-%d}T00:00:00{tz}", "to": f"{end:%Y-%m-%d}T00:00:00{tz}"},
            "filters": [],
        }
        settings = dict(self.section["settings"])   # ключ обязателен; пустой словарь — запрос без settings
        if settings:
            payload["settings"] = settings
        body = self.client.call("POST", ENDPOINT, params={"project": self.section["scopes"][scope]["project"]},
                                json=payload)
        if isinstance(body, dict) and body.get("status") == "error":
            raise SourceError(f"{ENDPOINT}: {body.get('error')} {body.get('description') or ''}".strip())
        rows, seen, merged = [], set(), {}
        for group in (body.get("data") or []) if isinstance(body, dict) else []:
            for item in group.get("items") or []:
                given = item.get("dimensions") or {}
                marker, second = (str((given.get(name) or {}).get("value") or "") for name in (DIMENSION, second_dimension))
                second = second if depth > 1 else ""
                values = {m.get("metric_name"): float(m.get("value") or 0) for m in item.get("metrics") or []}
                if depth > 1 and second_dimension == DIMENSION_PAGE:
                    second = self._page(second)
                    # Адреса одной страницы с разными хвостами («?utm=…») — одна страница: складываются, а не стоп.
                    if (marker, second) in merged:
                        for name, value in values.items():
                            merged[(marker, second)][name] = merged[(marker, second)].get(name, 0.0) + value
                        continue
                    merged[(marker, second)] = values
                if (marker, second) in seen:
                    raise GuardViolation(13, f"{ENDPOINT}: маркер «{self._label(marker, second, depth)}» повторяется "
                                             "в одном ответе — строки не складываются и не выбираются молча")
                seen.add((marker, second))
                rows.append((marker, second, values))
        return rows

    @staticmethod
    def _page(url: str) -> str:
        """«бризекс.рф/catalog?x=1#y» → «/catalog»; пустой адрес (поток без визита) остаётся пустым."""
        if not url:
            return ""
        path = url.split("://", 1)[-1]
        path = "/" + path.split("/", 1)[1] if "/" in path else "/"
        return path.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0].rstrip("/") or "/"

    @staticmethod
    def _label(marker: str, second: str, depth: int) -> str:
        first = marker or EMPTY_MARKER
        return first if depth == 1 else f"{first}{LEVEL_SEPARATOR}{second or EMPTY_MARKER}"

    @staticmethod
    def _in_flow(marker: str, no_visit: set, flow: str) -> bool:
        if flow == "all":
            return True
        return (marker in no_visit) == (flow == "no_visit")

    def fetch(self, query: Query) -> list[Number]:
        if query.breakdown not in (None, DIMENSION, DIMENSION_2, DIMENSION_PAGE):
            raise NotImplementedError(f"разрез «{query.breakdown}» пока не поддержан — только {DIMENSION}, "
                                      f"{DIMENSION_2} и {DIMENSION_PAGE}")
        depth = 2 if query.breakdown in (DIMENSION_2, DIMENSION_PAGE) else 1
        second_dimension = DIMENSION_PAGE if query.breakdown == DIMENSION_PAGE else DIMENSION_2
        if query.scope not in self.section["scopes"]:
            raise GuardViolation(9, f"кабинет «{query.scope}» не объявлен в конфигурации")
        if query.flow not in self.cfg.flows:
            raise GuardViolation(9, f"поток «{query.flow}» не объявлен в конфигурации")
        rule = self.cfg.rule(query.metric)
        names = self.section["metric_names"]
        metric_name, visits_name = names[query.metric][query.scope], names["visits"][query.scope]
        rows = self._rows(query.scope, sorted({metric_name, visits_name}), query.period_start, query.period_end, depth,
                          second_dimension)
        money = query.metric in self.section["money_metrics"]
        common = dict(metric=query.metric, level=rule.level, scope=query.scope, flow=query.flow,
                      period_start=query.period_start, period_end=query.period_end, as_of=query.as_of,
                      source=f"C:{SYSTEM}:{ENDPOINT}", unit=self.section["currency"] if money else "шт")
        if not rows:
            return [Number(**common, status=Status.NO_DATA, value=None, missing="источник не вернул строк разреза")]
        no_visit = set(self.section["flow_markers"]["no_visit"][query.scope])
        drift = sorted({marker for marker, _, values in rows if marker in no_visit and values.get(visits_name, 0) > 0})
        if drift:
            raise GuardViolation(13, f"Т3: у маркеров потока без визита появились визиты: {drift}",
                                 GuardViolation.COVERAGE)
        selected = [row for row in rows if self._in_flow(row[0], no_visit, query.flow)]
        if query.breakdown is not None:
            return [Number(**common, segment=f"{query.breakdown}={self._label(marker, second, depth)}",
                           status=Status.PROXY if money else Status.FACT, value=values.get(metric_name, 0.0),
                           missing="деньги по каналу зависят от атрибуции сквозной аналитики" if money else "")
                    for marker, second, values in sorted(selected, key=lambda row: row[:2])]
        value = sum(values.get(metric_name, 0.0) for _, _, values in selected)
        if money and query.flow != "all":
            return [Number(**common, status=Status.PROXY, value=value,
                           missing="деньги по потоку зависят от атрибуции сквозной аналитики")]
        return [Number(**common, status=Status.FACT, value=value)]

    def totals(self, scope: str, metrics, start: date, end: date) -> dict:
        """Итоги нескольких метрик кабинета за окно одним запросом: поток all, без разреза."""
        if scope not in self.section["scopes"]:
            raise GuardViolation(9, f"кабинет «{scope}» не объявлен в конфигурации")
        names = {metric: self.section["metric_names"][metric][scope] for metric in metrics}
        rows = self._rows(scope, sorted(set(names.values())), start, end)
        return {metric: sum(values.get(name, 0.0) for _, _, values in rows) for metric, name in names.items()}

    def probe(self) -> list[ProbeResult]:
        end = date.today()
        results = []
        for scope in self.section["scopes"]:
            try:
                rows = self._rows(scope, [self.section["metric_names"]["visits"][scope]], end - timedelta(days=1), end)
                results.append(ProbeResult(f"{SYSTEM}:{scope}", True, 200, f"строк разреза: {len(rows)}"))
            except SourceError as exc:
                results.append(ProbeResult(f"{SYSTEM}:{scope}", False, None, str(exc)))
        return results
