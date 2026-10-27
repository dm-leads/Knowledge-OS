"""Приёмка на контур (Т42.5): признак работы каждого контура карты — проверкой кодом по реестрам инстанса, только
чтение. Перечень контуров, признаков, видов известных ответов и тестов — `data/contours.yaml`; `check --contours`
печатает таблицу «контур → признак → да / нет / не подключено → известных ответов → тестов».
"""
from __future__ import annotations

from datetime import date, timedelta
from importlib import resources

import yaml

from . import bets, publications, store

YES, NO, OFF = "да", "нет", "не подключено"


def spec() -> dict:
    text = resources.files("content_engine.data").joinpath("contours.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["contours"]


def demand_fresh(instance):
    path = instance.file("demand_map")
    if not path.is_file():
        return OFF, "карта спроса не собрана"
    data = store.read_json(path, "demand_map")
    age = (instance.today - date.fromisoformat(data["report"]["date"])).days
    free = len(data.get("candidates") or [])
    return (YES if age <= 7 and free else NO), f"собрана {age} дн. назад, свободных кластеров {free}"


def stock_norm(instance):
    from ..steps import queue as queue_mod
    if not queue_mod.load(instance):
        return OFF, "очереди нет"
    out, _ = queue_mod.status(instance)
    return (YES if not out["below_norm"] else NO), f"запас {out['stock']} при норме {out['norm']}"


def packages_verified(instance):
    folder = instance.root / "data" / "briefs"
    if not folder.is_dir():
        return OFF, "пакетов статей нет"
    built = [p.parent.parent.name for p in folder.glob("*/package/manifest.json")]
    arrived = {r.get("id") for r in publications.read(instance) if r["event"] == "published"}
    old = [t for t in built if t not in arrived]
    return (YES if not old else NO), f"пакетов {len(built)}, без «доехало» {len(old)}"


def releases_recorded(instance):
    rows = [r for r in publications.read(instance) if r["event"] == "published"]
    if not rows:
        return OFF, "выпусков машиной нет"
    book = {r["id"] for r in bets.load(instance)}
    missing = [r.get("id") for r in rows if not r.get("day") or r.get("id") not in book]
    return (YES if not missing else NO), f"выпусков {len(rows)}, без дня или записи в книге {len(missing)}"


def proof_running(instance):
    folder = instance.root / "data" / "pulse"
    last = sorted(folder.glob("????-??-??.json")) if folder.is_dir() else []
    if not last:
        return OFF, "пульса ещё не было"
    stale_pulse = (instance.today - date.fromisoformat(last[-1].stem)).days > 1
    overdue = 0
    for rec in bets.load(instance):
        first = rec.get("published")
        for w in bets.WINDOWS:
            if first and date.fromisoformat(first) + timedelta(days=w) <= instance.today \
                    and str(w) not in (rec.get("measures") or {}):
                overdue += 1
    ok = not stale_pulse and overdue == 0
    return (YES if ok else NO), f"пульс {last[-1].stem}; окон без замера {overdue}"


def foundation(instance):
    runs = store.read_jsonl(instance.root / "data" / "schedule_runs.jsonl", missing_ok=True)
    if not instance.p["schedule"]["jobs"]:
        return OFF, "расписания в профиле нет"
    recent = [r for r in runs if r["start"][:10] >= (instance.today - timedelta(days=2)).isoformat()]
    return (YES if recent else NO), f"профиль версии {instance.p['schema_version']}; запусков за 2 дня {len(recent)}"


CHECKS = {"demand_fresh": demand_fresh, "stock_norm": stock_norm, "packages_verified": packages_verified,
          "releases_recorded": releases_recorded, "proof_running": proof_running, "foundation": foundation}


def table(instance) -> list[dict]:
    from ..steps.known_answers import load_cases
    try:
        cases = load_cases(instance)
    except Exception:
        cases = []
    out = []
    for key, c in spec().items():
        try:
            state, detail = CHECKS[c["check"]](instance)
        except Exception as exc:                     # один контур не мешает остальным
            state, detail = NO, f"проверка не прошла: {exc}"
        known = sum(1 for k in cases if k.get("kind", "gate") in c["known"])
        out.append({"contour": key, "name": c["name"], "sign": c["sign"], "state": state, "detail": detail,
                    "known": known, "tests": len(c["tests"])})
    return out


def render(rows: list[dict]) -> str:
    lines = ["Приёмка на контур", "| Контур | Признак работы | Работает | Подробно | Известных ответов | Тестов |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r['contour']} {r['name']} | {r['sign']} | {r['state']} | {r['detail']} | {r['known']} | {r['tests']} |"
              for r in rows]
    return "\n".join(lines)
