"""Keys.so — выдача, запросы своего домена и конкурентов, остаток квоты (Т4.1, Т4.2). Только чтение.

Правила API, которые адаптер соблюдает сам:
- кириллический домен передаётся как есть, не в punycode — иначе API домен не находит;
- ответ с `code: 202` — отчёт строится, клиент ждёт до 6 минут;
- не больше 10 запросов за 10 секунд;
- постраничный отчёт сортируется по двум полям, иначе строки на стыке страниц теряются или дублируются;
- 429: если суточная квота `analysis` выбрана — остановка без повтора; иначе это частота — одна пауза и повтор;
- `tools/check-top` — объект квоты на найденную фразу; отчёты по домену почти не тратят квоту.
"""
from __future__ import annotations

import time

from ..core.errors import NotFound, SourceError
from ..core.urls import norm_url
from .base import RETRY, STOP, ReadOnlyClient
from .serp import NOT_IN_BASE, OK, SerpResult, SiteKeyword

API = "https://api.keys.so"
CHECK_TOP, KEYWORDS, COMPARE, LIMITS = ("tools/check-top", "report/simple/organic/keywords", "report/compare",
                                        "limits/all")
BATCH = 100          # большие списки одним запросом API не принимает; по документации — до 3000
PER_PAGE = 1000
AI_ANSWERS = "report/simple/organic/ai-answers"
AI_PER_PAGE = 100


class KeyssoSerp:
    name = "keysso"

    def __init__(self, api_key: str, *, transport=None, sleep=time.sleep, clock=time.monotonic, batch: int = BATCH):
        self.sleep = sleep
        self.clock = clock
        self.batch = batch
        self._stamps: list[float] = []
        self.client = ReadOnlyClient(
            "Keys.so", API, {CHECK_TOP, KEYWORDS, COMPARE, LIMITS},
            {"X-Keyso-TOKEN": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            transport=transport, sleep=sleep, on_429=self._on_429)

    def _throttle(self):
        now = self.clock()
        self._stamps = [s for s in self._stamps if now - s < 10]
        if len(self._stamps) >= 9:
            self.sleep(10 - (now - self._stamps[0]) + 0.2)
        self._stamps.append(self.clock())

    def _call(self, method, endpoint, **kwargs):
        self._throttle()
        return self.client.call(method, endpoint, **kwargs)

    def _on_429(self) -> str:
        try:
            return STOP if self.remaining() <= 0 else RETRY
        except SourceError:
            return STOP

    def remaining(self) -> int:
        data = self._call("GET", LIMITS)
        analysis = data.get("analysis") or {}
        if "limit" not in analysis:
            raise SourceError("Keys.so limits/all: нет строки analysis")
        return int(analysis["limit"]) - int(analysis.get("usedLimit") or 0)

    def top(self, phrases: list[str], base: str) -> dict[str, SerpResult]:
        out: dict[str, SerpResult] = {}
        for start in range(0, len(phrases), self.batch):
            part = phrases[start:start + self.batch]
            try:
                answer = self._call("POST", CHECK_TOP, params={"base": base}, json={"list": part})
            except NotFound:
                answer = {}                 # 404 у check-top: ни одной фразы пачки нет в базе
            if not isinstance(answer, dict):
                raise SourceError(f"Keys.so check-top: неожиданный ответ {str(answer)[:120]}")
            found = {str(k).strip().lower(): v for k, v in answer.items() if isinstance(v, list)}
            for phrase in part:
                places = found.get(phrase.strip().lower())
                if places is None:
                    out[phrase] = SerpResult(phrase, base, NOT_IN_BASE)
                    continue
                top = []
                for place in places:
                    pos = int(place.get("pos") or 0)
                    if 1 <= pos <= 10:
                        url = place.get("url") or place.get("domain") or ""
                        top.append((norm_url(url), pos))
                out[phrase] = SerpResult(phrase, base, OK, sorted(top, key=lambda item: item[1]))
        return out

    def _pages(self, endpoint: str, params: dict, limit: int) -> list[dict]:
        rows, page, last = [], 1, 1
        while page <= last and len(rows) < limit:
            data = self._call("GET", endpoint, params={**params, "page": page, "per_page": PER_PAGE})
            chunk = data.get("data") if isinstance(data, dict) else None
            if not isinstance(chunk, list):
                raise SourceError(f"Keys.so {endpoint}: в ответе нет списка data")
            rows.extend(chunk)
            last = int(data.get("last_page") or 1)
            page += 1
        return rows[:limit]

    def site_keywords(self, domain: str, base: str, limit: int) -> list[SiteKeyword]:
        rows = self._pages(KEYWORDS, {"base": base, "domain": domain, "sort": "wsk|desc,word|asc"}, limit)
        out = []
        for row in rows:
            path = row.get("url") or ""
            url = norm_url(path if "://" in path or not path.startswith("/") else domain + path, domain)
            out.append(SiteKeyword(url, str(row.get("word") or ""), int(row.get("pos") or 0),
                                   int(row.get("wsk") or 0), _float(row.get("avbid"))))
        return out

    def competitor_keywords(self, competitor: str, own_domain: str, base: str, limit: int) -> list[dict]:
        """Запросы конкурента, по которым нашего домена нет (`incl` — пересечение, поэтому по одному конкуренту)."""
        rows = self._pages(COMPARE, {"base": base, "view": "organic", "incl": competitor, "excl": own_domain,
                                     "sort": "wsk|desc,word|asc"}, limit)
        return [{"query": str(r.get("word") or ""), "frequency": int(r.get("wsk") or 0),
                 "bid": _float(r.get("avbid"))} for r in rows]


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class KeyssoDemand:
    """Спрос из Keys.so (Т4.2): запросы своего домена со страницей и запросы конкурентов, которых нет у нас.

    Позиции и частоты Keys.so — «оценка» на месяц обновления базы. Отчёты по домену почти не тратят квоту.
    """
    name = "keysso"

    def __init__(self, serp, domain: str, base: str, competitors: list[str], as_of: str, *,
                 site_limit: int, competitor_limit: int):
        self.serp = serp
        self.domain = domain
        self.base = base
        self.competitors = competitors
        self.as_of = as_of
        self.site_limit = site_limit
        self.competitor_limit = competitor_limit

    def fetch(self, period_from: str, period_to: str) -> list[dict]:
        from ..core.number import ESTIMATE
        from .demand import demand_row
        rows = []
        for kw in self.serp.site_keywords(self.domain, self.base, self.site_limit):
            row = demand_row(kw.query, self.name, period_from, period_to, self.as_of, page=kw.url,
                             position=(kw.pos, ESTIMATE) if kw.pos else None, frequency=(kw.frequency, ESTIMATE))
            row["bid"] = kw.bid
            rows.append(row)
        for competitor in self.competitors:
            for kw in self.serp.competitor_keywords(competitor, self.domain, self.base, self.competitor_limit):
                row = demand_row(kw["query"], f"{self.name}:конкурент", period_from, period_to, self.as_of,
                                 frequency=(kw["frequency"], ESTIMATE))
                row["competitor"] = competitor
                rows.append(row)
        return rows


class KeyssoAi:
    """Цитирование сайта в ответах ИИ (Т37.2) — только чтение. Отчёт ответов Алисы (база `msk`) и трекер нейросетей,
    созданный человеком: в белом списке — только чтение цитируемости трекера по его id; методов создания, правки и
    запуска трекера у клиента нет (В16)."""
    name = "keysso"

    def __init__(self, api_key: str, *, tracker_id: str = "", transport=None, sleep=time.sleep):
        allowed = {AI_ANSWERS, LIMITS}
        self.tracker = f"ai_tracker/{tracker_id}/citation" if tracker_id else ""
        if self.tracker:
            allowed.add(self.tracker)
        # каждый HTTP-запрос — единица предела квоты: без повторов и без ожидания «отчёт готовится» (202 — вернуть
        # «готовится, повторить позже», а не опрашивать)
        self.client = ReadOnlyClient(
            "Keys.so", API, allowed,
            {"X-Keyso-TOKEN": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            transport=transport, sleep=sleep, retries=1, max_wait=0)

    def answers(self, domain: str, *, pages: int) -> dict:
        """Запросы, в ответах Алисы на которые домен — среди источников; `pages` — предел страниц отчёта (запросов)."""
        rows, total, used = [], None, 0
        for page in range(1, pages + 1):
            data = self.client.call("GET", AI_ANSWERS, params={"base": "msk", "domain": domain, "page": page,
                                                               "per_page": AI_PER_PAGE, "sort": "ws|desc"})
            used += 1
            total = data.get("total", total)
            rows += data.get("data") or []
            if page >= int(data.get("last_page") or 1):
                break
        return {"rows": rows, "total": total, "requests": self.client.calls}

    def tracker_citation(self) -> dict:
        if not self.tracker:
            raise NotFound("трекер нейросетей не указан (ai_visibility.tracker_id)")
        return self.client.call("GET", self.tracker)

