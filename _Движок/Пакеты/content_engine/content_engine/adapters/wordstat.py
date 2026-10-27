"""Wordstat и подсказки (А1, Т31.2–Т31.3, Д10): сначала Keys.so, при отказе — облако Яндекса.

Порядок источников — `sources.wordstat.order`; следующий берётся, только если предыдущий отказал (ключ не принят,
лимит, сеть, «не подключено»), и отчёт пишет, каким источником снято каждое число.
- `keysso` — частота без записи в аккаунт: `report/simple/keyword_dashboard` (ws, wsk) и похожие запросы с частотой
  `report/simple/similarkeys`. Помесячной динамики у этого пути нет — только онлайн-парсер, который создаёт проект в
  аккаунте (запись); пакет его не вызывает, ряд — «нет данных». Расход — счётчик запросов клиента в отчёте.
- `cloud` — Yandex Search API: `wordstat/topRequests` (частота фразы с хвостами и ассоциации) и `wordstat/dynamics`
  (помесячно за 24 месяца). Платно: цена запроса × число запросов — до вызова, через бюджет-гейт (Т17.2).
Числа Wordstat — показы, а не люди, с хвостами: число по подстроке показывается вместе с 10–15 примерами (Т31.3).
"""
from __future__ import annotations

from datetime import date, timedelta

from ..core import budget
from ..core.env_file import secret
from ..core.errors import AccessDenied, EngineError, QuotaExhausted, SourceError
from ..core.number import ESTIMATE, NO_DATA
from .base import ReadOnlyClient
from .demand import demand_row

KEYSSO_API = "https://api.keys.so"
DASHBOARD, SIMILAR, LIMITS = "report/simple/keyword_dashboard", "report/simple/similarkeys", "limits/all"
CLOUD_API = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
TOP, DYNAMICS = "topRequests", "dynamics"
SAMPLES = 15
FALLBACK = (SourceError, AccessDenied, QuotaExhausted, EngineError)


class KeyssoWordstat:
    name = "keysso"

    def __init__(self, instance):
        k = instance.p["sources"]["keysso"]
        if not k["enabled"]:
            raise SourceError("Keys.so не подключён (sources.keysso.enabled: false)")
        self.base = k["base"]
        self.client = ReadOnlyClient("Keys.so Wordstat", KEYSSO_API, {DASHBOARD, SIMILAR, LIMITS},
                                     {"X-Keyso-TOKEN": secret(instance.p, k["key_env"]), "Accept": "application/json"},
                                     transport=instance.transport)

    def requests_left(self) -> int | None:
        """Остаток лимита запросов API Keys.so (`limits/all`, поле `apiRequest`); не читается — None."""
        data = self.client.call("GET", LIMITS)
        item = data.get("apiRequest") if isinstance(data, dict) else None
        if not isinstance(item, dict) or "limit" not in item:
            return None
        return int(item["limit"]) - int(item.get("usedLimit") or 0)

    def frequency(self, phrase: str) -> float | None:
        data = self.client.call("GET", DASHBOARD, params={"keyword": phrase, "base": self.base})
        row = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else {}
        value = row.get("ws") if isinstance(row, dict) else None
        return float(value) if value not in (None, "") else None

    def similar(self, phrase: str, limit: int) -> list[tuple[str, float | None]]:
        data = self.client.call("GET", SIMILAR, params={"keyword": phrase, "base": self.base, "per_page": limit,
                                                        "page": 1, "sort": "ws|desc"})
        return [(r["word"], float(r["ws"]) if r.get("ws") not in (None, "") else None)
                for r in (data.get("data") or [])][:limit]


class CloudWordstat:
    name = "cloud"

    def __init__(self, instance):
        w = instance.p["sources"]["wordstat"]
        self.region = w["region"]
        self.folder = secret(instance.p, w["cloud_folder_env"]) if w["cloud_folder_env"] else ""
        self.client = ReadOnlyClient("Wordstat (облако)", CLOUD_API, {TOP, DYNAMICS},
                                     {"Authorization": "Api-Key " + secret(instance.p, w["cloud_key_env"]),
                                      "Content-Type": "application/json"}, transport=instance.transport)
        self.today = instance.today

    def _body(self, body: dict) -> dict:
        return {**body, "folderId": self.folder} if self.folder else body

    def top(self, phrase: str, n: int) -> tuple[list, list]:
        data = self.client.call("POST", TOP, json=self._body({"phrase": phrase, "numPhrases": str(n),
                                                              "regions": [self.region]}))
        pairs = lambda key: [(r["phrase"], float(r["count"])) for r in data.get(key) or []]   # noqa: E731
        return pairs("results"), pairs("associations")

    def dynamics(self, phrase: str, months: int = 24) -> list[tuple[str, float]]:
        end = self.today.replace(day=1) - timedelta(days=1)
        y, m = end.year, end.month - (months - 1)
        while m <= 0:
            y, m = y - 1, m + 12
        data = self.client.call("POST", DYNAMICS, json=self._body({
            "phrase": phrase, "period": "PERIOD_MONTHLY", "regions": [self.region],
            "fromDate": f"{date(y, m, 1):%Y-%m-%d}T00:00:00Z", "toDate": f"{end:%Y-%m-%d}T00:00:00Z"}))
        return [(r["date"][:7], float(r["count"])) for r in data.get("results") or []]


def seeds(instance) -> list[str]:
    return list(dict.fromkeys(w for d in instance.p["directions"] for w in d["words"] if w.strip()))


class WordstatDemand:
    """`DemandSource`: частота семян направлений и их хвостов; ряд по месяцам — у облака."""
    name = "wordstat"

    def __init__(self, instance, *, confirm=None, unattended=False, suggest=False):
        self.instance = instance
        self.confirm, self.unattended, self.suggest = confirm, unattended, suggest
        self.report = {"source": "", "fallback": [], "samples": {}, "dropped_no_frequency": 0, "requests": 0}

    def fetch(self, period_from: str, period_to: str) -> list[dict]:
        order = self.instance.p["sources"]["wordstat"]["order"]
        if not order:
            raise SourceError("Wordstat не подключён (sources.wordstat.order пуст)")
        for name in order:
            try:
                rows = (self._keysso if name == "keysso" else self._cloud)(period_from, period_to)
                self.report["source"] = name
                return rows
            except FALLBACK as exc:
                if isinstance(exc, budget.BudgetRefused):
                    raise
                self.report["fallback"].append(f"{name}: {exc}")
        raise SourceError("Wordstat: все источники отказали — " + "; ".join(self.report["fallback"]))

    def _row(self, phrase, value, source, period_from, period_to, history=None):
        row = demand_row(phrase, f"wordstat:{source}", period_from, period_to, self.instance.today.isoformat(),
                         frequency=(value, ESTIMATE))
        if history is not None:
            row["history"] = history
        return row

    def _keysso(self, period_from, period_to) -> list[dict]:
        ks = KeyssoWordstat(self.instance)
        limit = self.instance.p["sources"]["suggest"]["limit"]
        items = seeds(self.instance)
        planned = len(items) * (2 if self.suggest else 1) + 1     # +1 — сам запрос остатка
        left = ks.requests_left()                # квота — до запроса, как у выдачи (П4)
        self.report["planned"] = planned
        self.report["limit_left"] = left if left is not None else "не читается"
        if left is not None and planned > left:
            raise QuotaExhausted(f"Keys.so: нужно запросов {planned}, остаток лимита API {left}")
        rows = []
        for seed in items:
            value = ks.frequency(seed)
            if value is not None:
                rows.append(self._row(seed, value, "keysso", period_from, period_to,
                                      history={"status": NO_DATA, "why": "ряд по месяцам у Keys.so — только парсером "
                                                                         "с записью в аккаунт"}))
            if self.suggest:
                similar = ks.similar(seed, limit)
                self.report["samples"][seed] = [w for w, _ in similar[:SAMPLES]]
                for word, ws in similar:
                    if not ws:
                        self.report["dropped_no_frequency"] += 1
                        continue
                    rows.append(self._row(word, ws, "keysso", period_from, period_to))
        self.report["requests"] = ks.client.calls
        return rows

    def _cloud(self, period_from, period_to) -> list[dict]:
        cloud = CloudWordstat(self.instance)
        w = self.instance.p["sources"]["wordstat"]
        items = seeds(self.instance)
        calls = len(items) * 2
        price = round(w["cloud_price_rub"] * calls, 2)
        n = self.instance.p["sources"]["suggest"]["limit"]

        def run():
            rows = []
            for seed in items:
                results, associations = cloud.top(seed, n)
                history = [{"month": m, "count": c} for m, c in cloud.dynamics(seed)]
                self.report["samples"][seed] = [p for p, _ in results[:SAMPLES]]
                for phrase, count in results:
                    rows.append(self._row(phrase, count, "cloud", period_from, period_to,
                                          history=history if phrase == seed else None))
                if self.suggest:
                    for phrase, count in associations:
                        if not count:
                            self.report["dropped_no_frequency"] += 1
                            continue
                        rows.append(self._row(phrase, count, "cloud", period_from, period_to))
            return rows

        rows = budget.paid_call(self.instance, "спрос", "Wordstat облако", price, run, confirm=self.confirm,
                                unattended=self.unattended, what=f"Wordstat облако: {calls} запросов",
                                source=f"sources.wordstat.cloud_price_rub × {calls}")
        self.report["requests"] = cloud.client.calls
        return rows
