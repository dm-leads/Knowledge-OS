"""Сбор спроса (А1, Т6): `content-engine demand collect`.

Вызывает подключённые источники за окно профиля, склеивает одинаковые нормализованные запросы (сумма показов, список
источников, страницы, где запрос уже ранжируется), отсеивает брендовые, мусорные и ниже порога показов. Упавший
источник не обнуляет остальные: в отчёте он «нет данных» с причиной. Итог — `data/demand.jsonl` со сверкой чтением.
"""
from __future__ import annotations

import re
from datetime import timedelta

from ..adapters.demand import FileDemand, value_of
from ..core import store
from ..core.errors import EngineError, SourceError
from ..core.number import ESTIMATE, FACT, Number
from ..core.env_file import secret


def period(instance) -> tuple[str, str]:
    end = instance.today - timedelta(days=1)
    start = end - timedelta(days=instance.p["sources"]["window_days"] - 1)
    return start.isoformat(), end.isoformat()


def build_sources(instance) -> list:
    """Подключённые источники спроса. Ошибка сборки источника — в отчёт, не в остановку."""
    cfg, as_of = instance.p["sources"], instance.today.isoformat()
    out = []
    if cfg["gsc"]["enabled"]:
        def gsc():
            from ..adapters.gsc import SearchConsole, service_account_token
            token = service_account_token(cfg["gsc"]["key_file"])
            return SearchConsole(cfg["gsc"]["site_url"], token, as_of, transport=instance.transport)
        out.append(("gsc", gsc))
    if cfg["webmaster"]["enabled"]:
        def webmaster():
            from ..adapters.webmaster import Webmaster
            w = cfg["webmaster"]
            return Webmaster(w["user_id"], w["host_id"], secret(instance.p, w["token_env"]), as_of,
                             transport=instance.transport)
        out.append(("webmaster", webmaster))
    if cfg["keysso"]["enabled"]:
        def keysso():
            from ..adapters.keysso import KeyssoDemand
            k = cfg["keysso"]
            return KeyssoDemand(instance.serp(), instance.p["site"]["domains"][0], k["base"],
                                instance.p["search"]["competitors"], as_of, site_limit=k["site_keywords_limit"],
                                competitor_limit=k["competitor_keywords_limit"])
        out.append(("keysso", keysso))
    if cfg["demand_file"]["enabled"]:
        def demand_file():
            path = instance.root / cfg["demand_file"]["path"]
            return FileDemand(path, as_of)
        out.append(("demand_file", demand_file))
    return out


def collect(instance, sources=None) -> dict:
    """Собрать, склеить, отсеять, записать. `sources` — [(имя, фабрика)] для тестов."""
    period_from, period_to = period(instance)
    sources = sources if sources is not None else build_sources(instance)
    if not sources:
        raise EngineError("источники спроса не подключены: sources.*.enabled = false")
    raw, report = [], {"period": [period_from, period_to], "sources": {}, "dropped": {}}
    for name, factory in sources:
        try:
            source = factory()
            rows = source.fetch(period_from, period_to)
            if hasattr(source, "fetch_pages"):
                rows += source.fetch_pages(period_from, period_to)
            report["sources"][name] = {"status": "ок", "rows": len(rows)}
            raw += rows
        except SourceError as exc:
            report["sources"][name] = {"status": "нет данных", "rows": 0, "reason": str(exc)}
    merged = merge(instance, raw, period_from, period_to)
    kept, dropped = screen(instance, merged)
    report["merged"] = len(merged)
    report["glued"] = len(raw) - len(merged)
    report["dropped"] = {reason: len(rows) for reason, rows in dropped.items()}
    report["kept"] = len(kept)
    if raw and not kept:
        raise EngineError("после отсева не осталось ни одного запроса — проверьте пороги и брендовые слова")
    report["write"] = store.write_jsonl(instance.file("demand"), kept, source_count=len(raw))
    return report


def merge(instance, rows: list[dict], period_from: str, period_to: str) -> list[dict]:
    norm, as_of = instance.norm, instance.today.isoformat()
    groups: dict[str, dict] = {}
    for row in rows:
        key = norm.key(row["query"])
        text = key.canonical()
        group = groups.setdefault(text, {"query": row["query"], "key": key.to_list(), "key_text": text,
                                         "sources": [], "pages": [], "competitors": [], "_imp": 0.0,
                                         "_imp_known": False, "_freq": None, "_best": -1.0})
        source = row["source"].split(":")[0]
        if source not in group["sources"]:
            group["sources"].append(source)
        if "impressions" in row["numbers"] and not row["page"]:
            group["_imp"] += value_of(row, "impressions")
            group["_imp_known"] = True
        if "frequency" in row["numbers"]:
            group["_freq"] = max(group["_freq"] or 0, value_of(row, "frequency"))
        weight = max(value_of(row, "impressions"), value_of(row, "frequency"))
        if weight > group["_best"]:
            group["_best"], group["query"] = weight, row["query"]
        if row["page"]:
            group["pages"].append({"url": row["page"], "source": source,
                                   "position": value_of(row, "position", None),
                                   "impressions": value_of(row, "impressions", None),
                                   "frequency": value_of(row, "frequency", None), "bid": row.get("bid")})
        if row.get("competitor") and row["competitor"] not in group["competitors"]:
            group["competitors"].append(row["competitor"])
    out = []
    for group in groups.values():
        consoles = "+".join(s for s in group["sources"] if s in ("gsc", "webmaster")) or "консоли"
        imp = (Number(group["_imp"], period_from, period_to, f"{consoles}:показы", FACT, as_of).to_dict()
               if group["_imp_known"] else None)
        freq = (Number(group["_freq"], period_from, period_to, "keysso:wsk", ESTIMATE, as_of).to_dict()
                if group["_freq"] is not None else None)
        out.append({k: v for k, v in group.items() if not k.startswith("_")} | {"impressions": imp,
                                                                              "frequency": freq})
    return out


def screen(instance, rows: list[dict]) -> tuple[list[dict], dict]:
    p = instance.p
    brand = [instance.norm.key(w) for w in p["brand_words"]]
    junk = [re.compile(x, re.IGNORECASE) for x in p["sources"]["junk_patterns"]]
    minimum = p["thresholds"]["min_impressions"]
    kept, dropped = [], {"бренд": [], "мусор": [], "ниже порога показов": [], "пустой ключ": []}
    from ..core.normalize import Key
    for row in rows:
        key = Key.from_list(row["key"])
        if not key:
            dropped["пустой ключ"].append(row)
        elif any(b and b.similarity(key) == 1.0 for b in brand):
            dropped["бренд"].append(row)
        elif any(rx.search(row["query"]) for rx in junk):
            dropped["мусор"].append(row)
        elif row["frequency"] is None and row["impressions"] and row["impressions"]["value"] < minimum:
            dropped["ниже порога показов"].append(row)
        else:
            kept.append(row)
    return kept, dropped


def weight(row: dict) -> float:
    """Вес запроса для порядка: частота, иначе показы."""
    freq = (row.get("frequency") or {}).get("value")
    imp = (row.get("impressions") or {}).get("value")
    return float(freq if freq is not None else imp or 0)


def load(instance) -> list[dict]:
    return store.read_jsonl(instance.file("demand"))


def render(report: dict) -> str:
    lines = [f"Сбор спроса за {report['period'][0]} — {report['period'][1]}"]
    for name, item in report["sources"].items():
        lines.append(f"  {name}: {item['status']}, строк {item['rows']}"
                     + (f" — {item['reason']}" if item.get("reason") else ""))
    lines.append(f"склеено одинаковых: {report['glued']}, запросов после склейки: {report['merged']}")
    lines.append("отсеяно: " + ", ".join(f"{k} — {v}" for k, v in report["dropped"].items()))
    lines.append(f"в спросе: {report['kept']}; {report['write']}")
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("demand", help="спрос: collect")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("collect", help="собрать спрос из подключённых источников")
    add_profile(p)
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    report = collect(instance)
    text = render(report)
    (instance.reports_dir() / "Сбор спроса.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0
