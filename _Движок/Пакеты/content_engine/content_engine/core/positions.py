"""Позиции своих ключей (модуль Д1, Т34.1): снимок дня и тревоги против прошлого снимка.

Ключи — целевые ключи страниц карты спроса (`monitor.keys: demand_map`): запросы кластеров, которые страница держит в
топ-10, и записи выпуска. Источники — консоли (Р16): бесплатно, факт, с задержкой данных. Google — Search Console,
«страница × запрос» за 7 дней по последний день с данными; Яндекс — Вебмастер, запросы сайта целиком (разреза по
странице у API нет), окно 30 дней: данные сервиса запаздывают на недели (проба З52 волны 3), поэтому период строки —
тот, что запрошен, а свежесть показывает панель. Нет доступа к консоли — «не подключено», а не ноль.
"""
from __future__ import annotations

from datetime import timedelta

from . import store
from .errors import EngineError, SourceError
from .urls import norm_url

GOOGLE, YANDEX = "google", "yandex"
GSC_DAYS, GSC_LAG, WM_DAYS = 7, 3, 30


def own_keys(instance) -> dict[str, str]:
    """Запрос → адрес страницы, которая его держит (целевые ключи карты спроса)."""
    try:
        pages = store.read_json(instance.file("demand_map"), "demand_map", missing_ok=True)["records"]
    except EngineError:
        return {}
    out = {}
    for pg in pages:
        for q in pg.get("target_keys") or []:
            out.setdefault(q.strip().lower(), norm_url(pg["url"]))
    return out


def _gsc(instance, keys: dict, transport):
    from ..adapters.gsc import SearchConsole, service_account_token
    g = instance.p["sources"]["gsc"]
    to = instance.today - timedelta(days=GSC_LAG)
    frm = to - timedelta(days=GSC_DAYS - 1)
    sc = SearchConsole(g["site_url"], service_account_token(g["key_file"]), instance.today.isoformat(),
                       transport=transport)
    rows = []
    totals = sc._rows(frm.isoformat(), to.isoformat(), ["date"])
    traffic = {"clicks": sum(r.get("clicks", 0) for r in totals),
               "impressions": sum(r.get("impressions", 0) for r in totals),
               "period": f"{frm.isoformat()} — {to.isoformat()}", "status": "факт", "source": "gsc: по дням"}
    for r in sc.fetch_pages(frm.isoformat(), to.isoformat()):
        q = r["query"].strip().lower()
        if q not in keys:
            continue
        page = norm_url(r["page"]) if r.get("page") else ""
        if page and page != keys[q]:
            continue                          # чужая страница сайта по этому запросу — не позиция ключа
        rows.append(_row(instance, GOOGLE, q, keys[q], r, frm.isoformat(), to.isoformat(), "gsc"))
    return rows, traffic


def _webmaster(instance, keys: dict, transport):
    from ..adapters.webmaster import Webmaster
    from .env_file import secret
    w = instance.p["sources"]["webmaster"]
    to = instance.today - timedelta(days=1)
    frm = to - timedelta(days=WM_DAYS - 1)
    wm = Webmaster(w["user_id"], w["host_id"], secret(instance.p, w["token_env"]), instance.today.isoformat(),
                   transport=transport)
    got = wm.fetch(frm.isoformat(), to.isoformat())
    traffic = {"clicks": sum((r["numbers"].get("clicks") or {}).get("value") or 0 for r in got),
               "impressions": sum((r["numbers"].get("impressions") or {}).get("value") or 0 for r in got),
               "period": f"{frm.isoformat()} — {to.isoformat()}", "status": "оценка",
               "source": "webmaster: сумма популярных запросов (данные сервиса запаздывают)"}
    return [_row(instance, YANDEX, r["query"].strip().lower(), keys[r["query"].strip().lower()], r,
                 frm.isoformat(), to.isoformat(), "webmaster")
            for r in got if r["query"].strip().lower() in keys], traffic


def _row(instance, engine, query, url, r, frm, to, source):
    """Строка позиции из строки консоли (`adapters/demand.demand_row`: числа — в `numbers`)."""
    numbers = r.get("numbers") or {}
    value = (numbers.get("position") or {}).get("value")
    return {"date": instance.today.isoformat(), "engine": engine, "query": query, "url": url,
            "position": None if value is None else round(float(value), 1),
            "impressions": (numbers.get("impressions") or {}).get("value"),
            "period_from": frm, "period_to": to, "source": source, "status": "факт"}


def snapshot(instance, *, transport=None) -> dict:
    """Снимок дня: строки по поисковикам и блоки «не подключено» с причиной. Упавший источник не мешает другому."""
    transport = transport if transport is not None else instance.transport
    keys = own_keys(instance)
    src = instance.p["sources"]
    out = {"date": instance.today.isoformat(), "keys": len(keys), "rows": [], "engines": {}}
    if instance.p["monitor"]["positions"] == "none":
        out["engines"] = {e: {"status": "не подключено", "why": "monitor.positions: none"} for e in (GOOGLE, YANDEX)}
        return out
    if not keys:                              # трафик всё равно снимается; позиций ключей нет — так и пишется
        out["keys_note"] = "у страниц карты спроса нет целевых ключей — `content-engine map build`"
    for engine, enabled, fn, hint in ((GOOGLE, src["gsc"]["enabled"], _gsc, "sources.gsc"),
                                      (YANDEX, src["webmaster"]["enabled"], _webmaster, "sources.webmaster")):
        if not enabled:
            out["engines"][engine] = {"status": "не подключено", "why": f"включить {hint}"}
            continue
        try:
            rows, traffic = fn(instance, keys, transport)
        except (SourceError, OSError) as exc:
            out["engines"][engine] = {"status": "источник не ответил", "why": str(exc)}
            continue
        out["rows"] += rows
        out["engines"][engine] = {"status": "ок", "rows": len(rows), "traffic": traffic,
                                  "period": f"{rows[0]['period_from']} — {rows[0]['period_to']}" if rows else ""}
    return out


def save(instance, snap: dict) -> str:
    return store.append_jsonl(instance.file("positions"), snap["rows"]) if snap["rows"] else "позиций нет — не записано"


def previous(instance) -> list[dict]:
    """Строки последнего снимка до сегодняшнего дня."""
    rows = store.read_jsonl(instance.file("positions"), missing_ok=True)
    days = sorted({r["date"] for r in rows if r["date"] < instance.today.isoformat()})
    return [r for r in rows if days and r["date"] == days[-1]]


def alarms(instance, today_rows: list[dict], before: list[dict]) -> list[dict]:
    """Тревоги (Т34.1): падение на `monitor.drop` и больше; вход и выход из топов `monitor.tops`."""
    m = instance.p["monitor"]
    old = {(r["engine"], r["query"]): r["position"] for r in before if r["position"] is not None}
    out = []
    for r in today_rows:
        was, now = old.get((r["engine"], r["query"])), r["position"]
        if was is None or now is None:
            continue
        if now - was >= m["drop"]:
            out.append({"kind": "падение", "engine": r["engine"], "query": r["query"], "url": r["url"],
                        "was": was, "now": now})
        for top in m["tops"]:
            if was > top >= now:
                out.append({"kind": f"вход в топ-{top}", "engine": r["engine"], "query": r["query"],
                            "url": r["url"], "was": was, "now": now})
            elif was <= top < now:
                out.append({"kind": f"выход из топ-{top}", "engine": r["engine"], "query": r["query"],
                            "url": r["url"], "was": was, "now": now})
    return out
