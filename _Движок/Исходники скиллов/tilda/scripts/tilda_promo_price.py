"""Акционная цена на товарной странице Тильды — в ЧЕРНОВИКЕ, без публикации.

Механика: на странице виден ОДИН ценовой блок, остальные скрыты, не удалены.
Шаги: копия блока-образца → в копии меняются ровно 2 места (текст цены и ссылка кнопки #order:…=цена) →
"price" в разметке JSON-LD кода страницы → видимым остаётся только новый блок (по page_preview черновика).
Каждый шаг: снимок «до», запись, обратное чтение. Неожиданный ответ переключателя видимости — откат и стоп.

Запуск:
  python tilda_promo_price.py <projectid> <pageid> <alias> <образец_rec> "<цена в образце>" "<новая цена>" \
      <старая цена JSON-LD> <новая цена JSON-LD> <папка снимков> <price_blocks.json>
  python tilda_promo_price.py --visibility-only <projectid> <pageid> <alias> <оставить_rec> <папка> <price_blocks.json>
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


class Stop(Exception):
    pass


def connect():
    c = Client(URL, headers_for(VAR))
    init = c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "promo-price", "version": "1"}})
    c.protocol = init.get("protocolVersion")
    c.call("notifications/initialized", notify=True)
    return c


def tool(c, name, args):
    res = c.call("tools/call", {"name": name, "arguments": args})
    text = "".join(x.get("text", "") for x in res.get("content", []) if x.get("type") == "text")
    if res.get("isError"):
        raise Stop(f"{name}: {text[:200]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def save(d: Path, name: str, obj):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def visible(c, pid, page, recs):
    html = tool(c, "page_preview", {"projectid": pid, "pageid": page})
    html = html if isinstance(html, str) else json.dumps(html, ensure_ascii=False)
    return {r for r in recs if f"rec{r}" in html}


def toggle_to(c, pid, page, rec, want):  # want: "y" скрыт / "n" виден
    got = str(tool(c, "toggle_page_block_visibility", {"projectid": pid, "pageid": page, "recordid": rec})).strip().lower()
    if got != want:
        back = str(tool(c, "toggle_page_block_visibility", {"projectid": pid, "pageid": page, "recordid": rec})).strip().lower()
        raise Stop(f"toggle {rec}: ждали {want}, получили {got}; откат → {back}")
    return got


def only_one_visible(c, pid, page, keep, price_recs, log):
    recs = set(price_recs) | {keep}
    vis = visible(c, pid, page, recs)
    log.append(f"видны до: {sorted(vis)}")
    if keep not in vis:
        log.append(f"показать {keep}: {toggle_to(c, pid, page, keep, 'n')}")
    for r in sorted(vis - {keep}):
        log.append(f"скрыть {r}: {toggle_to(c, pid, page, r, 'y')}")
    final = visible(c, pid, page, recs)
    log.append(f"видны после: {sorted(final)}")
    if final != {keep}:
        raise Stop(f"после переключений видны {sorted(final)}, ждали только {keep}")


def price_recs_of(path, page):
    for p in json.loads(Path(path).read_text(encoding="utf-8")):
        if p["pageid"] == page:
            return [b["recordid"] for b in p["blocks"]]
    raise Stop(f"страницы {page} нет в {path}")


def main() -> int:
    log = []
    try:
        if sys.argv[1] == "--visibility-only":
            pid, page, alias, keep, d, pb = sys.argv[2:8]
            pid, page, keep, d = int(pid), int(page), int(keep), Path(d)
            c = connect()
            only_one_visible(c, pid, page, keep, price_recs_of(pb, page), log)
        else:
            pid, page, alias, tpl, tprice, nprice, old_ld, new_ld, d, pb = sys.argv[1:11]
            pid, page, tpl, d = int(pid), int(page), int(tpl), Path(d)
            c = connect()
            base = {"projectid": pid, "pageid": page}
            head = tool(c, "get_head_code", base)["page"]["headcode"]
            save(d, "head_before.json", {"headcode": head})
            needle = f'"price": "{old_ld}"'
            if head.count(needle) != 1:
                raise Stop(f"в разметке {head.count(needle)} вхождений {needle}, ждали 1")
            src = tool(c, "get_zero_block", {**base, "recordid": tpl})
            save(d, f"zero_{tpl}_template.json", src)
            new = tool(c, "duplicate_record", {**base, "recordid": tpl})["recordid"]
            log.append(f"копия {tpl} → {new}")
            z = tool(c, "get_zero_block", {**base, "recordid": new})
            save(d, f"zero_{new}_copy.json", z)
            if {k: v for k, v in z.items() if k != "timestamp"} != {k: v for k, v in src.items() if k != "timestamp"}:
                raise Stop("копия не совпадает с образцом")
            changes = []
            for k, e in z.items():
                if not k.isdigit():
                    continue
                for f in ("text", "caption", "link"):
                    v = e.get(f)
                    if isinstance(v, str) and tprice in v:
                        e[f] = v.replace(tprice, nprice)
                        changes.append(f"{k}.{f}")
                    if isinstance(v, str) and "%" in v:  # процент прошлой акции в копии — проверить глазами
                        log.append(f"ВНИМАНИЕ: «%» в {k}.{f}")
            links = [x for x in changes if x.endswith(".link")]
            if len(changes) != 2 or len(links) != 1:
                raise Stop(f"ждали 2 правки (цена + кнопка), нашли {changes}; копия {new} осталась без правок")
            save(d, f"zero_{new}_NEW.json", z)
            tool(c, "update_zero_block", {**base, "recordid": new, "code": z})
            back = tool(c, "get_zero_block", {**base, "recordid": new})
            if any(z[k] != back.get(k) for k in z if k != "timestamp"):
                raise Stop(f"обратное чтение блока {new} не совпало")
            log.append(f"блок {new}: {tprice} → {nprice} в {changes}, обратное чтение ок")
            head2 = head.replace(needle, f'"price": "{new_ld}"')
            tool(c, "update_page_head_code", {**base, "headcode": head2})
            if tool(c, "get_head_code", base)["page"]["headcode"] != head2:
                raise Stop("обратное чтение разметки не совпало")
            log.append(f"разметка: {old_ld} → {new_ld}, обратное чтение ок")
            only_one_visible(c, pid, page, new, price_recs_of(pb, page) + [tpl], log)
        print(f"OK {alias}: " + " | ".join(log))
        save(d, "log.json", log)
        return 0
    except Stop as e:
        print(f"STOP {sys.argv[3] if len(sys.argv) > 3 else ''}: {e} | " + " | ".join(log))
        return 1


if __name__ == "__main__":
    sys.exit(main())
