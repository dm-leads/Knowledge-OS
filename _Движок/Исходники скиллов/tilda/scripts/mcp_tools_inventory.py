"""Опись инструментов http-MCP-сервера: имя, метки readOnly/destructive, описание. Только чтение.

Вызывает initialize, tools/list и, для инструментов-заглушек с подсказкой «Call describe_tool»,
tools/call describe_tool (если сервер его даёт). Ничего не меняет на сервере.

Запуск:
  python mcp_tools_inventory.py <URL> <ПЕРЕМЕННАЯ_С_ТОКЕНОМ> <выходной.json>
Токен берётся помощником mcp_bearer_header.py и не печатается.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def headers_for(var: str) -> dict:
    out = subprocess.run(
        [sys.executable, str(HERE / "mcp_bearer_header.py"), var],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


class Client:
    def __init__(self, url: str, auth: dict):
        self.url, self.auth, self.session, self.n, self.protocol = url, auth, None, 0, None

    def call(self, method: str, params: dict | None = None, notify: bool = False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.n += 1
            body["id"] = self.n
        h = {**self.auth, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        data = json.dumps(body).encode()
        for attempt in range(6):
            req = urllib.request.Request(self.url, data=data, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    self.session = r.headers.get("Mcp-Session-Id") or self.session
                    raw = r.read().decode("utf-8")
                break
            except urllib.error.HTTPError as e:
                err = e.read()
                if e.code == 429 and attempt < 5:  # лимит частоты: ждём до retry_at
                    try:
                        retry_at = json.loads(err)["error"]["data"]["retry_at"]
                        wait = max(1.0, min(120.0, retry_at - time.time() + 1))
                    except (ValueError, KeyError, TypeError):
                        wait = 10.0 * (attempt + 1)
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"{method}: HTTP {e.code} {err[:300]!r}") from None
        if notify or not raw.strip():
            return None
        if raw.lstrip().startswith("{"):
            msg = json.loads(raw)
        else:  # SSE: берём последнее data:
            datas = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")]
            msg = json.loads(datas[-1])
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg["result"]


def main() -> int:
    url, var, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    c = Client(url, headers_for(var))
    init = c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "inventory", "version": "1"}})
    c.protocol = init.get("protocolVersion")
    c.call("notifications/initialized", notify=True)
    tools, cursor = [], None
    while True:
        res = c.call("tools/list", {"cursor": cursor} if cursor else {})
        tools += res.get("tools", [])
        cursor = res.get("nextCursor")
        if not cursor:
            break
    names = {t["name"] for t in tools}
    rows = []
    for t in tools:
        desc, ann = t.get("description", ""), t.get("annotations") or {}
        if "describe_tool" in names and "Call describe_tool" in desc and t["name"] != "describe_tool":
            r = c.call("tools/call", {"name": "describe_tool", "arguments": {"tool_name": t["name"]}})
            text = "".join(x.get("text", "") for x in r.get("content", []) if x.get("type") == "text")
            try:
                full = json.loads(text)
                desc = full.get("description", desc)
                ann = full.get("annotations") or ann
            except json.JSONDecodeError:
                pass
        rows.append({"name": t["name"], "readOnlyHint": ann.get("readOnlyHint"),
                     "destructiveHint": ann.get("destructiveHint"),
                     "openWorldHint": ann.get("openWorldHint"), "title": ann.get("title") or t.get("title"),
                     "description": desc})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"server": init.get("serverInfo"), "count": len(rows), "tools": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    back = json.loads(out.read_text(encoding="utf-8"))
    print(f"server={init.get('serverInfo', {}).get('version')} written={len(rows)} read_back={back['count']}")
    return 0 if rows and back["count"] == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
