"""Подмена HTTP для адаптера Keys.so: ответы в формате API, собранные из синтетической фикстуры."""
from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "serp.json"


class Response:
    def __init__(self, status: int, data=None, text: str = ""):
        self.status_code = status
        self._data = data
        self.text = text or (json.dumps(data, ensure_ascii=False) if data is not None else "")
        self.headers = {}
        self.encoding = "utf-8"

    def json(self):
        return self._data


class KeyssoTransport:
    """Отвечает как Keys.so; `script` — очередь подмен на конкретный адрес: {"tools/check-top": [Response, ...]}."""

    def __init__(self, data: dict | None = None, *, per_page: int = 2, script: dict | None = None):
        self.data = data or json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.per_page = per_page
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.log: list[tuple[str, str]] = []

    def __call__(self, method, url, params=None, json=None, headers=None, timeout=None):
        endpoint = url.split("api.keys.so/", 1)[1]
        self.log.append((method, endpoint))
        if self.script.get(endpoint):
            return self.script[endpoint].pop(0)
        params = params or {}
        if endpoint == "limits/all":
            return Response(200, {"analysis": {"limit": 30000, "usedLimit": 30000 - self.data["remaining"]}})
        if endpoint == "tools/check-top":
            table = self.data["top"].get(params["base"], {})
            answer = {}
            for phrase in json["list"]:
                if phrase in table:
                    answer[phrase] = [{"domain": u.split("/")[0], "url": u, "pos": p} for u, p in table[phrase]]
            return Response(200, answer)
        if endpoint == "report/simple/organic/keywords":
            rows = [{"word": r["query"], "url": "/" + r["url"].split("/", 1)[1], "pos": r["pos"],
                     "wsk": r["frequency"], "avbid": r["bid"]} for r in self.data["site_keywords"][params["base"]]]
            return self._page(rows, int(params["page"]))
        if endpoint == "report/compare":
            rows = [{"word": r["query"], "wsk": r["frequency"], "avbid": r["bid"]}
                    for r in self.data["competitor_keywords"].get(params["incl"], [])]
            return self._page(rows, int(params["page"]))
        return Response(404, {"message": "нет такого адреса"})

    def _page(self, rows, page):
        last = max(1, -(-len(rows) // self.per_page))
        chunk = rows[(page - 1) * self.per_page: page * self.per_page]
        return Response(200, {"data": chunk, "current_page": page, "last_page": last, "total": len(rows)})
