"""Запись ОДНОГО блока Тильды из подготовленного файла с проверкой обратным чтением.

Только черновик блока (update_record / update_zero_block); публикацию страниц скрипт не делает.
Запуск:
  python tilda_apply_block.py zero   <projectid> <pageid> <recordid> <снимок_до.json> <новый_code.json>
  python tilda_apply_block.py record <projectid> <pageid> <recordid> <снимок_до.json> <новые_поля.json> <only_field>
  python tilda_apply_block.py head   <projectid> <pageid> 0 <снимок_до.json> <{"headcode": ...}.json>
Защиты в коде: без существующего снимка «до» не пишет; record — только один поле (only_field) вкладки content;
после записи читает блок заново и сверяет с отправленным; расхождение — код выхода 1.
Каждую запись вносить в журнал действий.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_tools_inventory import Client, headers_for  # noqa: E402

URL = "https://tilda.ru/api/mcp/"
VAR = os.environ.get("TILDA_MCP_VAR", "")  # ИМЯ переменной с токеном проекта, напр. TILDA_MCP_KEY_<САЙТ>
if not VAR:
    sys.exit("set TILDA_MCP_VAR=<имя переменной с токеном Тильды> (значение токена — в файле секретов)")


def tool(c: Client, name: str, args: dict):
    res = c.call("tools/call", {"name": name, "arguments": args})
    text = "".join(x.get("text", "") for x in res.get("content", []) if x.get("type") == "text")
    if res.get("isError"):
        raise RuntimeError(f"{name}: {text[:300]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def main() -> int:
    mode, pid, page, rec, before, new = sys.argv[1:7]
    pid, page, rec = int(pid), int(page), int(rec)
    if not Path(before).is_file():
        sys.stderr.write("refused: no 'before' snapshot\n")
        return 3
    payload = json.loads(Path(new).read_text(encoding="utf-8"))
    c = Client(URL, headers_for(VAR))
    init = c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "apply-block", "version": "1"}})
    c.protocol = init.get("protocolVersion")
    c.call("notifications/initialized", notify=True)
    base = {"projectid": pid, "pageid": page, "recordid": rec}
    if mode == "zero":
        r = tool(c, "update_zero_block", {**base, "code": payload})
        back = tool(c, "get_zero_block", base)
        diff = [k for k in payload if k != "timestamp" and payload.get(k) != back.get(k)]
    elif mode == "record":
        field = sys.argv[7]
        if set(payload) != {field}:
            sys.stderr.write("refused: payload must contain exactly the only_field\n")
            return 3
        r = tool(c, "update_record", {**base, "tab": "content", "only_field": field, "fields": payload})
        back = tool(c, "edit_record", {**base, "tab": "content"})["record"]
        sent, got = payload[field], back.get(field)
        if field == "list":
            sent, got = json.loads(sent), json.loads(got or "[]")
        diff = [] if sent == got else [field]
    elif mode == "head":  # код страницы в <head>: payload = {"headcode": "..."}; rec не используется
        if set(payload) != {"headcode"}:
            sys.stderr.write("refused: payload must be {\"headcode\": ...}\n")
            return 3
        r = tool(c, "update_page_head_code", {"projectid": pid, "pageid": page, "headcode": payload["headcode"]})
        back = tool(c, "get_head_code", {"projectid": pid, "pageid": page}).get("page", {}).get("headcode")
        diff = [] if back == payload["headcode"] else ["headcode"]
    elif mode == "product":  # товар магазина — ЖИВЫЕ данные; rec = productuid; payload = {"price","priceold"}
        if set(payload) != {"price", "priceold"}:
            sys.stderr.write("refused: payload must be {price, priceold}\n")
            return 3
        snap = json.loads(Path(before).read_text(encoding="utf-8"))["result"]
        if str(snap.get("uid")) != str(rec):
            sys.stderr.write("refused: snapshot is of another product\n")
            return 3
        cur = tool(c, "store_get_products", {"projectid": pid, "productuid": rec})
        if cur != snap:
            sys.stderr.write("refused: product changed since snapshot\n")
            return 3
        r = tool(c, "store_save_products", {"projectid": pid, "productuid": rec, "title": snap["title"],
                                            "gallery": snap.get("gallery") or "[]", **payload})
        back = tool(c, "store_get_products", {"projectid": pid, "productuid": rec})
        diff = [k for k in set(snap) | set(back)
                if k not in ("price", "priceold", "updated", "changed", "date_upd")
                and snap.get(k) != back.get(k)]
        if float(back.get("price") or 0) != float(payload["price"]) or \
                float(back.get("priceold") or 0) != float(payload["priceold"] or 0):
            diff.append("price/priceold not applied")
    else:
        sys.stderr.write("mode must be zero|record|head|product\n")
        return 2
    print(f"{mode} rec={rec} response={str(r)[:80]!r} read_back_diff={diff}")
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main())
