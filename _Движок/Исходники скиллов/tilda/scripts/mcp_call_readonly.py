"""Вызов ЧИТАЮЩЕГО инструмента http-MCP-сервера с сохранением ответа в файл (снимок «как было»).

Запуск:
  python mcp_call_readonly.py <URL> <ПЕРЕМЕННАЯ_С_ТОКЕНОМ> <инструмент> '<аргументы JSON>' <выходной.json>
Пишущие инструменты запрещены в коде: разрешены только имена из белого списка ниже.
Токен берёт помощник mcp_bearer_header.py и не печатает.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_tools_inventory import Client, headers_for  # noqa: E402

READ_ONLY = re.compile(
    r"^(get_[a-z0-9_]+|list_[a-z0-9_]+|stats_get_[a-z0-9_]+|store_get_[a-z0-9_]+|feeds_get_[a-z0-9_]+"
    r"|edit_record|page_preview|describe_tool|tpls_list|tpls_categories)$"
)


def main() -> int:
    url, var, tool, args, out = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4]), Path(sys.argv[5])
    if not READ_ONLY.match(tool):
        sys.stderr.write(f"refused: {tool} is not in the read-only whitelist\n")
        return 3
    c = Client(url, headers_for(var))
    init = c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "readonly-call", "version": "1"}})
    c.protocol = init.get("protocolVersion")
    c.call("notifications/initialized", notify=True)
    res = c.call("tools/call", {"name": tool, "arguments": args})
    text = "".join(x.get("text", "") for x in res.get("content", []) if x.get("type") == "text")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = text
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tool": tool, "args": args, "isError": res.get("isError", False),
                               "result": payload}, ensure_ascii=False, indent=1), encoding="utf-8")
    back = json.loads(out.read_text(encoding="utf-8"))
    print(f"{tool} isError={back['isError']} bytes={out.stat().st_size}")
    return 1 if back["isError"] else 0


if __name__ == "__main__":
    sys.exit(main())
