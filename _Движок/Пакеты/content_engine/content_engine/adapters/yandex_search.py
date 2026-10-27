"""Yandex Search API — запасной источник выдачи (Н6, `SerpSource`): если Keys.so отказал, взятие темы и гейт не встают.

`POST https://searchapi.api.cloud.yandex.net/v2/web/search` с телом `{query: {searchType, queryText}, region,
responseFormat: FORMAT_XML}` → `rawData` — Яндекс XML в base64; первые десять `<url>` — топ выдачи. Регион базы —
`sources.yandex_search.regions` (например, `msk: "213"`). Ключ и каталог облака — именами переменных. Платно: цена
запроса × число фраз — до вызова, через бюджет-гейт; флаги `--confirm-spend` и `--unattended` приходят из команды через
инстанс. Запросов своего домена у источника нет — «не подключено» (их дают консоли).

`FallbackSerp` — порядок `sources.serp_order`: следующий источник берётся только при отказе предыдущего (ключ не
принят, лимит, сеть), отчёт видит, кто ответил (`last_source`).
"""
from __future__ import annotations

import base64
import re

from ..core import budget
from ..core.env_file import secret
from ..core.errors import AccessDenied, QuotaExhausted, SourceError
from .base import ReadOnlyClient
from .serp import NOT_IN_BASE, OK, SerpResult

API = "https://searchapi.api.cloud.yandex.net/v2/web"
SEARCH = "search"
URL = re.compile(r"<url>\s*([^<\s]+)\s*</url>", re.I)


class YandexSearchSerp:
    name = "yandex_search"

    def __init__(self, instance):
        y = instance.p["sources"]["yandex_search"]
        self.instance = instance
        self.regions = y["regions"]
        self.price = y["price_rub"]
        self.folder = secret(instance.p, y["folder_env"]) if y["folder_env"] else ""
        self.client = ReadOnlyClient("Yandex Search API", API, {SEARCH},
                                     {"Authorization": "Api-Key " + secret(instance.p, y["key_env"]),
                                      "Content-Type": "application/json"}, transport=instance.transport)

    def _one(self, phrase: str, base: str) -> SerpResult:
        region = self.regions.get(base)
        if not region:
            raise SourceError(f"Yandex Search API: базы «{base}» нет в sources.yandex_search.regions")
        body = {"query": {"searchType": "SEARCH_TYPE_RU", "queryText": phrase}, "region": region,
                "responseFormat": "FORMAT_XML"}
        if self.folder:
            body["folderId"] = self.folder
        data = self.client.call("POST", SEARCH, json=body)
        raw = data.get("rawData") or ""
        try:
            xml = base64.b64decode(raw).decode("utf-8", "replace") if raw else ""
        except ValueError as exc:
            raise SourceError(f"Yandex Search API: rawData не base64 — {exc}") from exc
        urls = URL.findall(xml)[:10]
        return SerpResult(phrase, base, OK if urls else NOT_IN_BASE, [(u, n) for n, u in enumerate(urls, 1)])

    def top(self, phrases: list[str], base: str) -> dict[str, SerpResult]:
        cost = round(self.price * len(phrases), 2)

        def run():
            return {p: self._one(p, base) for p in phrases}
        # платный источник — всегда через гейт, и при цене 0
        return budget.paid_call(self.instance, "выдача", "выдача Yandex Search API", cost, run,
                                confirm=getattr(self.instance, "confirm_spend", None),
                                unattended=getattr(self.instance, "unattended", False),
                                what=f"выдача {len(phrases)} фраз", source=f"sources.yandex_search.price_rub × {len(phrases)}")

    def site_keywords(self, domain: str, base: str, limit: int):
        raise SourceError("Yandex Search API: запросов своего домена нет — не подключено (их дают консоли)")

    def remaining(self) -> int:
        return 10 ** 9                  # суточной квоты объектов нет: предел — деньги, его держит бюджет-гейт


class FallbackSerp:
    """Источники выдачи по порядку: следующий — только при отказе предыдущего."""

    def __init__(self, factories: list):
        self.factories = factories        # [(имя, фабрика)]
        self._built = {}
        self.last_source = ""
        self.notes: list[str] = []
        self.name = "→".join(n for n, _ in factories)

    def _source(self, name, factory):
        if name not in self._built:
            self._built[name] = factory()
        return self._built[name]

    def _try(self, method: str, *args):
        errors = []
        for name, factory in self.factories:
            try:
                result = getattr(self._source(name, factory), method)(*args)
                self.last_source = name
                if errors:
                    self.notes.append(f"выдача: {'; '.join(errors)} — ответил {name}")
                return result
            except (AccessDenied, QuotaExhausted, SourceError) as exc:
                errors.append(f"{name} отказал ({exc})")
        raise SourceError("все источники выдачи отказали: " + "; ".join(errors))

    def top(self, phrases, base):
        return self._try("top", phrases, base)

    def site_keywords(self, domain, base, limit):
        return self._try("site_keywords", domain, base, limit)

    def remaining(self) -> int:
        return self._try("remaining")

    @property
    def calls(self):
        return sum(getattr(getattr(s, "client", None), "calls", 0) for s in self._built.values())
