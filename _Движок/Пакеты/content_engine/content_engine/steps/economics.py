"""Экономика статьи и закрытие направления (Т40): `content-engine economics`.

По каждой статье книги ставок — цена (учёт расхода Т17.3: рубли, объекты Keys.so, токены; «нет данных» по работе
агента — цена «неполная», не ноль) против денег страницы за окно 180 дней (замер `bet measure`; окно не наступило —
так и пишется). По направлению — сумма цен, сумма денег, окупаемость; статус суммы — наихудший из слагаемых.
Предложение «закрыть направление» (1.6.0 — с порогом шума): только по окну 180 и только по записям со ставкой —
их с наступившим окном `economics.close_window` не меньше `close_min_articles`, заявок за окно по ступеням 1–2 не
больше `close_leads_max`, сумма ожиданий в заявках не меньше `bets.NOISE_MIN_EVENTS`, а недобор больше шума
(`bets.noise`). Окно короче 180 — только «предварительный сигнал»: рост статьи нелинейный. Записи без ставки
закрытию не помогают — сравнивать не с чем. Proxy и «нет данных» — «мало данных», закрытие не предлагается.
Решение — Движок роста и человек; машина только предлагает.
"""
from __future__ import annotations

from collections import defaultdict

from ..core import bets, spend
from ..core.number import NO_DATA

RANK = {"нет данных": 0, "proxy": 1, "оценка": 2, "факт": 3}


def _cost(rows: list[dict], tid: str) -> dict:
    mine = [r for r in rows if r["id"] == tid]
    rub = sum(float(r["value"]) for r in mine if r["kind"] == "rub" and r["status"] != NO_DATA)
    missing = sum(1 for r in mine if r["status"] == NO_DATA)
    estimate = any(r["status"] == "оценка" for r in mine)
    return {"rub": round(rub, 2), "keysso_objects": sum(float(r["value"]) for r in mine
                                                        if r["kind"] == "keysso_objects" and r["status"] != NO_DATA),
            "complete": missing == 0 and bool(mine), "no_data": missing, "known": bool(mine),
            "status": "оценка" if estimate else ("факт" if mine else "нет данных")}


def report(instance) -> dict:
    e = instance.p["economics"]
    rows = spend.rows(instance)
    articles, by_dir = [], defaultdict(lambda: {"cost": 0.0, "money": 0.0, "articles": 0, "status": 3,
                                                 "incomplete": 0, "matured": 0, "leads": 0.0, "low_data": 0,
                                                 "money_known": 0, "cost_known": 0, "money_missing": 0,
                                                 "matured_bet": 0, "expected": 0.0, "leads_bet": 0.0, "no_number": 0})
    for rec in bets.load(instance):
        cost = _cost(rows, rec["id"])
        m180 = (rec.get("measures") or {}).get("180")
        mclose = (rec.get("measures") or {}).get(str(e["close_window"]))
        money = m180["money"] if m180 else None
        line = {"id": rec["id"], "url": rec["url"], "direction": rec.get("direction") or "без направления",
                "cost": cost, "money_180": money,
                "note": "" if m180 else (f"окно не наступило, замер {(rec.get('measure') or {}).get('180', '—')}"
                                         if rec.get("published") else "день выкладки не установлен")}
        articles.append(line)
        d = by_dir[line["direction"]]
        d["articles"] += 1
        d["cost"] += cost["rub"]
        d["cost_known"] += cost["known"]
        d["incomplete"] += not cost["complete"]
        if m180 and (not money or money.get("value") is None):
            d["money_missing"] += 1
        if money and money.get("value") is not None:
            d["money"] += float(money["value"])
            d["money_known"] += 1
            d["status"] = min(d["status"], RANK.get(money["status"], 0))
        if mclose:
            d["matured"] += 1
            leads = mclose.get("leads") or {}
            known = (mclose["step"] <= 2 and leads.get("value") is not None
                     and leads.get("status") not in ("proxy", "нет данных"))
            if known:
                d["leads"] += float(mclose["leads"]["value"])
            else:
                d["low_data"] += 1
            if not rec.get("bet_missing"):
                d["matured_bet"] += 1
                value, unit, _ = bets.expectation_of(rec)
                if value is None or unit != "leads":
                    d["no_number"] += 1
                else:
                    d["expected"] += value * e["close_window"] / 180
                    if known:
                        d["leads_bet"] += float(mclose["leads"]["value"])
    directions, proposals = [], []
    status_name = {v: k for k, v in RANK.items()}
    for name, d in sorted(by_dir.items()):
        # денег не известно ни по одной статье — «нет данных», а не ноль и не «факт»
        status = status_name[d["status"]] if d["money_known"] else "нет данных"
        payback = round(d["money"] / d["cost"], 2) if d["cost"] and d["money_known"] else None
        directions.append({"direction": name, **d, "status": status, "payback": payback})
        proposal = _close(name, d, e)
        if proposal:
            proposals.append(proposal)
    return {"articles": articles, "directions": directions, "proposals": proposals}


def _close(name: str, d: dict, e: dict) -> str:
    """Строка о закрытии направления или пусто (1.6.0: окно 180, только ставки, порог шума)."""
    if d["matured"] < e["close_min_articles"] or d["leads"] > e["close_leads_max"]:
        return ""
    if d["matured_bet"] == 0:
        return (f"направление «{name}»: заявок за {e['close_window']} дней {d['leads']:g} у {d['matured']} статей, "
                "но ставок нет — закрытие данными невозможно, решение человека")
    if d["matured_bet"] < e["close_min_articles"] or d["low_data"] or d["no_number"]:
        return ""
    nz = bets.noise(d["expected"])
    signal = (f"заявок за {e['close_window']} дней {d['leads_bet']:g} при ожидании {d['expected']:g} "
              f"по {d['matured_bet']} ставкам (шум ±{nz:.1f})")
    if d["expected"] < bets.NOISE_MIN_EVENTS or d["expected"] - d["leads_bet"] <= nz:
        return f"направление «{name}»: {signal} — недобор в пределах шума, закрывать рано"
    if e["close_window"] < 180:
        return (f"предварительный сигнал: направление «{name}» — {signal}; вывод о закрытии — по окну 180 "
                "(рост статьи нелинейный)")
    return (f"предложение: закрыть направление «{name}» — {signal}; решение — Движок роста и человек, квоты "
            "переходят другим направлениям")


def render(data: dict) -> str:
    lines = ["# Экономика статей", "", "| Статья | Направление | Цена, ₽ | Деньги за 180 дней | Примечание |",
             "|---|---|---|---|---|"]
    for a in data["articles"]:
        c, m = a["cost"], a["money_180"]
        cost = (f"{c['rub']:g}" + ("" if c["complete"] else f" (неполная: нет данных {c['no_data']})")
                if c["known"] else "нет данных")
        money = "—" if m is None else ("нет данных" if m.get("value") is None
                                       else f"{m['value']:g} ({m['status']}, {m['source']})")
        lines.append(f"| {a['id']} | {a['direction']} | {cost} | {money} | {a['note']} |")
    lines += ["", "| Направление | Статей | Цена, ₽ | Деньги, ₽ | Окупаемость | Статус |", "|---|---|---|---|---|---|"]
    for d in data["directions"]:
        cost = "нет данных" if not d["cost_known"] else (f"{d['cost']:g}" + (f" (неполная у {d['incomplete']})"
                                                                              if d["incomplete"] else ""))
        money = (f"{d['money']:g}" + (f" (нет данных у {d['money_missing']})" if d["money_missing"] else "")
                 if d["money_known"] else "нет данных")
        lines.append(f"| {d['direction']} | {d['articles']} | {cost} | {money} | "
                     f"{d['payback'] if d['payback'] is not None else '—'} | {d['status']} |")
    lines += [""] + (data["proposals"] or ["Предложений и сигналов о закрытии направлений нет."])
    return "\n".join(lines) + "\n"


def register(sub):
    from ..cli import add_profile
    p = sub.add_parser("economics", help="экономика статей: цена против денег за 180 дней, окупаемость направлений")
    add_profile(p)
    p.add_argument("--unattended", action="store_true", help="запуск без человека (расписание); платных вызовов нет")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    data = report(instance)
    text = render(data)
    (instance.reports_dir() / "economics.md").write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0
