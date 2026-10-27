"""Книга ставок с деньгами (модуль Д2, Б1, Т39): `content-engine bet backfill | measure | accept | propose`.

- `backfill` — статьи корпуса, которых нет в книге, получают запись задним числом: день выкладки из шапки статьи (роль
  `date`), иначе первый день с показами в Search Console («восстановлен: gsc»), иначе «не установлен» (не
  замеряется). Ставки у них нет — итог «ставки нет», деньги страницы меряются. Ключ сверки — адрес: статья машины
  второй записи не получает. Без `--apply` — план.
- `measure` — по каждому окну 90 и 180: не наступило — «окно не наступило, замер <дата>»; наступило — деньги и заявки
  за окно «день выкладки → +N» (`adapters/money_windows.py`) и предложенный итог (`core/bets.decide`). Итог ставки
  ставит только человек (`accept`).
- `accept <id> --window --who` — человек принимает итог окна (или ставит свой с `--result`).
- `propose` — по принятым итогам со ставкой из гипотезы — файл-предложение для Движка роста в формате его листа чисел
  (`bets.proposal_dir`). В реестр Движка машина не пишет: число и замер заводит Движок своими командами.
Все записи книги — под блокировкой `bets/write` (`core/bets.update`): книгу пишет и чат контента.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import yaml

from ..core import bets
from ..core.errors import EngineError, GateRefused, SourceError
from ..core.urls import norm_url, section_of, slug_of


def _first_days(instance, urls: list[str]) -> dict[str, str]:
    """День выкладки задним числом из Search Console: первый день с показами (Т22.5)."""
    g = instance.p["sources"]["gsc"]
    if not g["enabled"] or not urls:
        return {}
    from ..adapters.gsc import SearchConsole, service_account_token
    try:
        sc = SearchConsole(g["site_url"], service_account_token(g["key_file"]), instance.today.isoformat(),
                           transport=instance.transport)
    except (SourceError, OSError, ValueError):
        return {}
    since = (instance.today - timedelta(days=480)).isoformat()      # предел истории Search Console — 16 месяцев
    out = {}
    for url in urls:
        try:
            day = sc.first_day(url if url.startswith("http") else "https://" + url, since, instance.today.isoformat())
        except (SourceError, OSError):
            continue
        if day:
            out[url] = day
    return out


def backfill(instance, *, apply: bool, since: str | None = None) -> dict:
    section = instance.p["bets"]["backfill_section"]
    pages = [pg for pg in instance.corpus()[0] if not section or section_of(pg.url) == section]
    known = {norm_url(r["url"]) for r in bets.load(instance)}
    todo = [pg for pg in pages if norm_url(pg.url) not in known]
    undated = [pg.url for pg in todo if not pg.date]
    restored = _first_days(instance, undated)
    plan, sources = [], {"из шапки статьи": 0, "восстановлен: gsc": 0, "не установлен": 0}
    for pg in todo:
        day, how = (pg.date, "из шапки статьи") if pg.date else (
            (restored[pg.url], "восстановлен: gsc") if pg.url in restored else ("", "не установлен"))
        if since and day and day < since:
            continue
        sources[how] += 1
        rec = {"id": f"C:{pg.slug}", "origin": "corpus", "topic": pg.title, "url": norm_url(pg.url),
               "hypothesis": "", "hypothesis_id": "", "expectation": "", "fail_threshold": "",
               "published": day or None, "published_source": how, "result": None, "bet_missing": True,
               "direction": _direction(instance, pg.title)}
        if day:
            first = date.fromisoformat(day)
            rec["measure"] = {str(w): (first + timedelta(days=w)).isoformat() for w in bets.WINDOWS}
        plan.append(rec)
    out = {"pages": len(pages), "new": len(plan), "sources": sources, "write": ""}
    if apply and plan:
        with bets.update(instance) as box:
            have = {norm_url(r["url"]) for r in box["records"]}
            box["records"] += [r for r in plan if r["url"] not in have]
        out["write"] = box["write"]
    return out


def _direction(instance, title: str) -> str:
    from .demand_map import direction_of
    return direction_of(instance, instance.norm.key(title))


def measure(instance, *, only: str | None = None, as_of: date | None = None) -> dict:
    from ..adapters.money_windows import MoneyWindows
    today = as_of or instance.today
    money = MoneyWindows(instance)
    min_step = instance.p["bets"]["min_step"]
    counts = {"окно не наступило": 0, "замерено": 0, "уже замерено": 0, "без дня выкладки": 0}
    lines = []
    with bets.update(instance) as box:
        for rec in box["records"]:
            if only and rec["id"] != only:
                continue
            if not rec.get("published"):
                counts["без дня выкладки"] += 1
                continue
            rec.setdefault("measures", {})
            first = date.fromisoformat(rec["published"])
            for window in bets.WINDOWS:
                key, end = str(window), first + timedelta(days=window)
                if end > today:
                    counts["окно не наступило"] += 1
                    continue
                old = rec["measures"].get(key)
                if old and (old.get("result") is not None or old.get("proposed") != bets.LOW_DATA):
                    counts["уже замерено"] += 1
                    continue
                got = money.money(rec["url"], first.isoformat(), (end - timedelta(days=1)).isoformat())
                measured = {"step": got["step"], "money": got["money"].to_dict(),
                            "leads": got["leads"].to_dict() if got["leads"] else None}
                result, why = bets.decide(rec, measured, min_step)
                rec["measures"][key] = {**measured, "proposed": result, "why": why,
                                        "measured_on": today.isoformat(), "result": None}
                counts["замерено"] += 1
                lines.append(f"{rec['id']} +{key}: {result} — {why}")
    upcoming = sorted((v, r["id"], k) for r in box["records"] for k, v in (r.get("measure") or {}).items()
                      if v and date.fromisoformat(v) > today)
    return {"counts": counts, "lines": lines, "upcoming": upcoming[:10], "write": box["write"]}


def accept(instance, tid: str, window: str, who: str, result: str | None, reason: str | None) -> str:
    if not who.strip():
        raise EngineError("--who — имя человека, который принимает итог")
    with bets.update(instance) as box:
        rec = next((r for r in box["records"] if r["id"] == tid), None)
        if rec is None:
            raise EngineError(f"в книге ставок нет записи {tid}")
        m = (rec.get("measures") or {}).get(window)
        if m is None:
            raise GateRefused(f"{tid} +{window}: замера нет — сначала `bet measure`")
        final = result or m["proposed"]
        if final not in bets.RESULTS + (bets.NO_BET_RESULT,):
            raise EngineError(f"итог — одно из {bets.RESULTS}")
        if result and result != m["proposed"] and not (reason or "").strip():
            raise EngineError("итог не как предложено — нужна причина: --reason")
        m.update(result=final, accepted_by=who, accepted_on=instance.today.isoformat(), reason=reason or "")
    return f"{tid} +{window}: итог «{final}» принят ({who}); {box['write']}"


def _number_row(rec: dict, window: str, m: dict, unit: str) -> dict:
    """Строка листа чисел Движка роста (`growth_engine/core/storage.py`, NUMBER_FIELDS)."""
    num = m["money"] if unit == "money" else (m.get("leads") or {})
    return {"metric": "выручка по странице входа" if unit == "money" else "заявки по странице входа",
            "level": "статья", "scope": rec["url"], "flow": "", "segment": rec.get("direction", ""),
            "period_start": num.get("period_from"), "period_end": num.get("period_to"), "as_of": num.get("as_of"),
            "source": num.get("source"), "status": num.get("status"), "value": num.get("value"),
            "denominator": "", "unit": "₽" if unit == "money" else "заявки", "missing": ""}


def propose(instance) -> dict:
    folder = Path(instance.p["bets"]["proposal_dir"])
    folder = folder if folder.is_absolute() else instance.root / folder
    groups: dict[tuple[str, str], list] = {}
    for rec in bets.load(instance):
        for window, m in (rec.get("measures") or {}).items():
            if m.get("result") in bets.RESULTS and rec.get("hypothesis_id") and not m.get("proposed_file"):
                groups.setdefault((rec["hypothesis_id"], window), []).append((rec, m))
    if not groups:
        raise GateRefused("принятых итогов ставок из гипотез без предложения нет — сначала `bet accept`")
    written = []
    for (hid, window), items in groups.items():
        _, unit, _ = bets.expectation_of(items[0][0])
        unit = unit or "money"
        data = {"hypothesis_id": hid, "window": int(window), "made_on": instance.today.isoformat(),
                "results": [{"id": r["id"], "url": r["url"], "result": m["result"], "accepted_by": m["accepted_by"]}
                            for r, m in items],
                "numbers": [_number_row(r, window, m, unit) for r, m in items],
                "how_to": f"Движок роста: завести числа на лист чисел и `measure {hid} measured_id=<номер числа>`; "
                          "статус гипотезы меняет только Движок"}
        path = folder / f"{instance.today.isoformat()}-{hid}-{window}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")
        if yaml.safe_load(path.read_text(encoding="utf-8"))["hypothesis_id"] != hid:
            raise EngineError(f"{path.name}: предложение не прочиталось обратно")
        written.append(str(path))
    with bets.update(instance) as box:
        for rec in box["records"]:
            for window, m in (rec.get("measures") or {}).items():
                if (rec.get("hypothesis_id"), window) in groups and m.get("result") in bets.RESULTS:
                    m["proposed_file"] = instance.today.isoformat()
    return {"files": written, "write": box["write"]}


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("bet", help="книга ставок: backfill | measure | accept | propose")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("backfill", help="статьи корпуса в книгу ставок задним числом; без --apply — план")
    add_profile(p)
    p.add_argument("--since", help="только статьи с днём выкладки не раньше ГГГГ-ММ-ДД")
    p.add_argument("--apply", action="store_true", help="записать в data/bets.json (файл инстанса)")
    p.set_defaults(func=run)
    p = actions.add_parser("measure", help="замер окон 90 и 180 дней: деньги и заявки страницы, предложенный итог")
    add_profile(p)
    p.add_argument("--id", help="одна запись книги")
    p.add_argument("--as-of", help="день замера ГГГГ-ММ-ДД (по умолчанию сегодня)")
    p.add_argument("--unattended", action="store_true", help="запуск без человека (расписание); платных вызовов нет")
    p.set_defaults(func=run)
    p = actions.add_parser("accept", help="человек принимает итог окна ставки")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--window", required=True, choices=[str(w) for w in bets.WINDOWS])
    p.add_argument("--who", required=True, help="имя человека")
    p.add_argument("--result", choices=list(bets.RESULTS), help="свой итог вместо предложенного (с --reason)")
    p.add_argument("--reason", help="почему итог не как предложено")
    p.set_defaults(func=run)
    p = actions.add_parser("propose", help="файл-предложение для Движка роста по принятым итогам")
    add_profile(p)
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "backfill":
        out = backfill(instance, apply=args.apply, since=args.since)
        print(f"Книга ставок задним числом: страниц корпуса {out['pages']}, новых записей {out['new']}; день выкладки: "
              + ", ".join(f"{k} — {v}" for k, v in out["sources"].items()))
        print(out["write"] or ("план: выполнить с --apply" if out["new"] else "добавлять нечего"))
        return 0
    if args.action == "measure":
        out = measure(instance, only=args.id, as_of=date.fromisoformat(args.as_of) if args.as_of else None)
        text = "\n".join([f"Замер ставок: " + ", ".join(f"{k} — {v}" for k, v in out["counts"].items())]
                         + [f"  {line}" for line in out["lines"]]
                         + ["Ближайшие замеры: " + ("; ".join(f"{d} — {i} (+{w})" for d, i, w in out["upcoming"])
                                                    or "нет")] + [out["write"]])
        (instance.reports_dir() / "bets.md").write_text(text + "\n", encoding="utf-8", newline="\n")
        print(text)
        return 0
    if args.action == "accept":
        print(accept(instance, args.id, args.window, args.who, args.result, args.reason))
        return 0
    out = propose(instance)
    print("Предложения для Движка роста:\n" + "\n".join(f"  {f}" for f in out["files"]) + f"\n{out['write']}")
    return 0
