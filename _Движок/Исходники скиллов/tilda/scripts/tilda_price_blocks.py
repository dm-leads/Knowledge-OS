"""Разведка ценовых блоков на страницах Тильды (только чтение).

Для каждой страницы: все Zero Block с кнопкой «#order:…=цена», цена, зачёркнутая цена, подпись блока,
виден ли блок на живой странице, и "price" из разметки JSON-LD в коде страницы.
Запуск: python tilda_price_blocks.py <projectid> <выходной.json> <pageid>:<alias> [<pageid>:<alias> ...]
alias — путь страницы без домена (catalog/model); домен берётся из get_projects. Нужна TILDA_MCP_VAR.
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_tools_inventory import Client, headers_for  # noqa: E402

URL = "https://tilda.ru/api/mcp/"
VAR = os.environ.get("TILDA_MCP_VAR", "")  # ИМЯ переменной с токеном проекта, напр. TILDA_MCP_KEY_<САЙТ>
if not VAR:
    sys.exit("set TILDA_MCP_VAR=<имя переменной с токеном Тильды> (значение токена — в файле секретов)")


def tool(c, name, args):
    res = c.call("tools/call", {"name": name, "arguments": args})
    text = "".join(x.get("text", "") for x in res.get("content", []) if x.get("type") == "text")
    if res.get("isError"):
        raise RuntimeError(f"{name}: {text[:200]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def plain(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def main() -> int:
    pid, out, pages = int(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    c = Client(URL, headers_for(VAR))
    init = c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "price-blocks", "version": "1"}})
    c.protocol = init.get("protocolVersion")
    c.call("notifications/initialized", notify=True)
    site = next(p["url"] for p in tool(c, "get_projects", {}) if str(p["id"]) == str(pid)).rstrip("/") + "/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    report = []
    for spec in pages:
        page, alias = spec.split(":", 1)
        page = int(page)
        live = opener.open(site + alias, timeout=30).read().decode("utf-8", "replace")
        recs = tool(c, "get_page_records", {"projectid": pid, "pageid": page})
        head = tool(c, "get_head_code", {"projectid": pid, "pageid": page}).get("page", {}).get("headcode", "")
        prices_ld = re.findall(r'"price"\s*:\s*"([^"]*)"', head)
        blocks = []
        for i, r in enumerate(recs):
            if str(r["tplid"]) != "396":
                continue
            try:
                z = tool(c, "get_zero_block", {"projectid": pid, "pageid": page, "recordid": r["recordid"]})
            except RuntimeError as e:  # альяс на Zero Block другой страницы
                if "not ZeroBlock" not in str(e):
                    raise
                continue
            els = {k: v for k, v in z.items() if k.isdigit()}
            order = [(k, e["link"]) for k, e in els.items() if str(e.get("link", "")).startswith("#order:")]
            if not order:
                continue
            texts = [(k, plain(e.get("text")), e.get("hidden"), "line-through" in (e.get("text") or ""))
                     for k, e in els.items() if e.get("elem_type") == "text" and "₽" in (e.get("text") or "")]
            blocks.append({"recordid": r["recordid"], "position": i, "title": z.get("title", ""),
                           "live_visible": f'id="rec{r["recordid"]}"' in live,
                           "order": order, "price_texts": texts})
        report.append({"pageid": page, "alias": alias, "jsonld_price": prices_ld, "blocks": blocks})
        print(f"{alias}: price-blocks={len(blocks)} jsonld={prices_ld}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    back = json.loads(out.read_text(encoding="utf-8"))
    print(f"written pages={len(report)} read_back={len(back)}")
    return 0 if len(back) == len(pages) else 1


if __name__ == "__main__":
    sys.exit(main())
