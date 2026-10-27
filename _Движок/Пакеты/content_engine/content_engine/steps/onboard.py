"""Совет по темпу и приёмка запуска (Т32): `queue status` советует темп; `content-engine onboard` — первый проход.

Темп (Т32.1): возраст домена (`queue.pace.domain_since`), скорость индексации (доля новых адресов в индексе к 7-му
дню — по записям `index status` в журнале выпуска) и запас. Домен моложе `new_domain_days` и ритм выше
`new_domain_max_per_day` — предупреждение. Чего нет — «не установлено» / «нет данных», а не ноль.

`onboard` (Т32.2): годность ниши — частотность Wordstat семян направлений по региону против
`onboard.min_cluster_frequency` (оценка; Wordstat не подключён — «не подключено», а не «годна»), кто в топе (доля
коммерческих доменов в кэше выдачи), есть ли цена заявки (`sources.value`), узкий ли регион; светофор `check`; вёрстка
выборки живых страниц; технический чек-лист (`tech_checklist.yaml`: есть — пункты на ручную проверку, нет — «не
подключено», строится в волне 3); `queue build`. Два решения человека — режим вычитки (`facts.mode`) и ритм
(`queue.per_day`) — пишутся в профиль только с `--answer`; без ответа — вопросы и код 2.
"""
from __future__ import annotations

import re
from datetime import date

from ..core import publications
from ..core.errors import EngineError

OFF = "не подключено"


def pace(instance) -> dict:
    p = instance.p["queue"]
    since = p["pace"]["domain_since"]
    age = (instance.today - date.fromisoformat(since)).days if since else None
    checks = [r for r in publications.read(instance) if r["event"] == "index_status" and r.get("age", 0) >= 7]
    latest = {}
    for r in checks:
        latest[r["url"]] = r
    indexed = [r for r in latest.values() if r.get("google_in") or r.get("yandex_in")]
    speed = f"{len(indexed)} из {len(latest)} новых адресов в индексе к 7-му дню" if latest else "нет данных"
    warn = ""
    if age is not None and age < p["pace"]["new_domain_days"] and p["per_day"] > p["pace"]["new_domain_max_per_day"]:
        warn = (f"домен молодой ({age} дн. < {p['pace']['new_domain_days']}): ритм {p['per_day']} в день выше совета "
                f"{p['pace']['new_domain_max_per_day']} — снизить")
    return {"domain_age_days": age, "domain_age": f"{age} дн." if age is not None else "не установлен "
            "(queue.pace.domain_since)", "indexing_speed": speed, "warning": warn}


def set_line(path, section: str, key: str, value: str) -> str:
    """Поменять значение одного поля раздела верхнего уровня текстом: комментарии и порядок профиля остаются."""
    from pathlib import Path
    from ..core import migrate, profile as profile_mod
    path = Path(path)
    text = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    start, stop = migrate._section_path(lines, section)
    child, _, end = migrate._child_span(lines, start, stop)
    i = migrate._find_key(lines, start + 1, end, child, key)
    if i is None:
        raise EngineError(f"поля «{section}.{key}» нет в профиле")
    m = re.match(r"^(\s*[^:]+:\s*)([^#]*?)(\s*#.*)?$", lines[i])
    lines[i] = f"{m.group(1)}{value}{m.group(3) or ''}"
    new = newline.join(lines)
    profile_mod.validate(migrate.parse(new))
    path.write_bytes(new.encode("utf-8"))
    if path.read_bytes().decode("utf-8") != new:
        raise EngineError(f"{path.name}: записано — прочитано другим")
    return f"{section}.{key} = {value}"


def niche_fitness(instance, *, transport=None) -> list[dict]:
    p = instance.p
    out = []
    w = p["sources"]["wordstat"]
    if not w["order"]:
        out.append({"what": "спрос", "value": OFF, "status": "нет данных",
                    "why": "Wordstat не подключён (sources.wordstat.order) — годность не доказана"})
    else:
        from ..adapters.wordstat import WordstatDemand
        try:
            rows = WordstatDemand(instance).fetch(instance.today.isoformat(), instance.today.isoformat())
            from ..adapters.wordstat import seeds
            total = sum(r["numbers"]["frequency"]["value"] for r in rows if r["query"] in seeds(instance))
            fit = total >= p["onboard"]["min_cluster_frequency"]
            out.append({"what": "спрос", "value": f"{int(total)} показов в месяц по семенам направлений",
                        "status": "оценка", "why": ("годна" if fit else "мало") + f": порог "
                        f"{p['onboard']['min_cluster_frequency']}; источник Wordstat, регион {w['region']}"})
        except Exception as exc:                          # спрос не снят — честно, а не «годна»
            out.append({"what": "спрос", "value": "нет данных", "status": "нет данных", "why": str(exc)})
    import json
    try:
        commercial = set(p["search"]["commercial_domains"])
        tops = []
        for path in (instance.root / "cache" / "serp").glob("*/*.json"):
            result = json.loads(path.read_text(encoding="utf-8")).get("result") or {}
            if result.get("status") == "ok":
                from ..adapters.serp import SerpResult
                tops.append(SerpResult.from_dict(result))
        share = (sum(1 for r in tops for url, _ in r.top if any(d in url for d in commercial))
                 / max(1, sum(len(r.top) for r in tops)))
        out.append({"what": "кто в топе", "value": f"доля коммерческих доменов {share:.0%}" if tops else "нет данных",
                    "status": "факт" if tops else "нет данных", "why": "кэш выдачи" if tops else "кэш выдачи пуст"})
    except Exception as exc:
        out.append({"what": "кто в топе", "value": "нет данных", "status": "нет данных", "why": str(exc)})
    v = p["sources"]["value"]
    out.append({"what": "цена заявки", "value": {"file": "деньги или заявки по страницам", "keysso": "proxy Keys.so",
                                                 "none": "нет"}[v["kind"]],
                "status": {"file": "факт", "keysso": "proxy", "none": "нет данных"}[v["kind"]],
                "why": "sources.value"})
    out.append({"what": "регион", "value": "вся Россия" if w["region"] in ("225", "") else f"регион {w['region']}",
                "status": "факт", "why": "узкий регион — спрос меньше; сравнить с порогом" if w["region"] not in
                ("225", "") else "широкий"})
    return out


def onboard(instance, answers: dict[str, str]) -> tuple[dict, int]:
    from . import check as check_mod
    from . import queue as queue_mod
    report = {"niche": niche_fitness(instance), "pace": pace(instance)}
    items = check_mod.run_check(instance)
    report["check"] = {light: sum(1 for i in items if i["light"] == light)
                       for light in (check_mod.GREEN, check_mod.YELLOW, check_mod.RED, check_mod.OFF)}
    tech = instance.root / "tech_checklist.yaml"
    report["tech"] = f"чек-лист есть: {tech} — пройти пункты" if tech.is_file() else f"{OFF} (строится в волне 3)"
    report["layout"] = OFF if not instance.p["publish"]["browser"] else "вёрстка выборки — `publish verify` страниц"
    try:
        built = queue_mod.build(instance)
        report["queue"] = f"кандидатов {built['candidates']}, добавлено {built['added']}; пополнение — `queue refill`"
    except EngineError as exc:
        report["queue"] = f"очередь не собрана: {exc}"
    questions = {"vychitka": "Режим вычитки фактов под нишу: separate (вторая модель) или human (человек)?",
                 "rhythm": "Ритм выпуска — статей в день (например, 0.57 = 4 в неделю)?"}
    missing = [k for k in questions if not answers.get(k)]
    if missing:
        report["questions"] = [questions[k] for k in missing]
        return report, 2
    if answers["vychitka"] not in ("separate", "human"):
        raise EngineError("--answer vychitka=separate|human")
    try:
        per_day = float(answers["rhythm"].replace(",", "."))
    except ValueError as exc:
        raise EngineError("--answer rhythm=<статей в день>") from exc
    report["written"] = [set_line(instance.profile_path, "facts", "mode", answers["vychitka"]),
                         set_line(instance.profile_path, "queue", "per_day", str(per_day))]
    return report, 0


def register(sub):
    from ..cli import add_profile
    p = sub.add_parser("onboard", help="приёмка запуска: годность ниши, светофор, очередь, два решения человека")
    add_profile(p)
    p.add_argument("--answer", action="append", default=[], help="vychitka=separate|human, rhythm=<статей в день>")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    answers = dict(a.split("=", 1) for a in args.answer if "=" in a)
    report, code = onboard(instance, answers)
    print("Приёмка запуска")
    for row in report["niche"]:
        print(f"  {row['what']}: {row['value']} ({row['status']}) — {row['why']}")
    pc = report["pace"]
    print(f"  темп: возраст домена {pc['domain_age']}; индексация: {pc['indexing_speed']}"
          + (f"; ! {pc['warning']}" if pc["warning"] else ""))
    print("  светофор: " + ", ".join(f"{k} — {v}" for k, v in report["check"].items()))
    print(f"  технический чек-лист: {report['tech']}; вёрстка: {report['layout']}; очередь: {report['queue']}")
    for q in report.get("questions", []):
        print(f"  ? {q}")
    for line in report.get("written", []):
        print(f"  записано в профиль: {line}")
    return code
