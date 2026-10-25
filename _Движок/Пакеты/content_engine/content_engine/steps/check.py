"""Самопроверка (Т14.1): `content-engine check` — светофор по каждому пункту, с причиной и подсказкой.

Пункты: профиль; секреты по именам; каждый источник отвечает пробой (Keys.so — остаток квоты, консоли — минимальный
запрос); корпус читается; свежесть карты спроса и кэша выдачи; запас очереди. Цвет — зелёный, жёлтый, красный или
«не подключено». Отказ одного пункта не останавливает остальные: отозванный ключ — красный пункт, а не падение.
"""
from __future__ import annotations

import json
from datetime import date

from ..core import store
from ..core.errors import AccessDenied, EngineError, QuotaExhausted, SourceError
from ..core.secrets import has_secret, secret

GREEN, YELLOW, RED, OFF = "зелёный", "жёлтый", "красный", "не подключено"


def _item(name, light, detail, hint=""):
    return {"item": name, "light": light, "detail": detail, "hint": hint}


def _probe(name, fn):
    try:
        return fn()
    except AccessDenied as exc:
        return _item(name, RED, f"доступ отозван — {exc}", "выпустить новый ключ и записать его в файл секретов")
    except QuotaExhausted as exc:
        return _item(name, YELLOW, f"квота выбрана — {exc}", "повторить после обновления лимита")
    except (SourceError, EngineError, OSError, ValueError) as exc:
        return _item(name, RED, str(exc), "проверить настройки источника в профиле")


def run_check(instance) -> list[dict]:
    p = instance.p
    items = [_item("профиль", GREEN, f"{instance.profile_path.name} прошёл проверку")]
    src = p["sources"]
    names = {"keysso": src["keysso"]["key_env"], "webmaster": src["webmaster"]["token_env"]}
    for source, env in names.items():
        if not src[source]["enabled"]:
            items.append(_item(f"секрет {source}", OFF, "источник выключен",
                               f"sources.{source}.enabled: true и переменная {env} в файле секретов"))
        elif has_secret(p, env):
            items.append(_item(f"секрет {source}", GREEN, f"переменная {env} найдена"))
        else:
            items.append(_item(f"секрет {source}", RED, f"переменной {env} нет", "добавить её в файл секретов"))

    def keysso():
        left = instance.serp().remaining()
        reserve = p["budget"]["reserve"]
        light = GREEN if left > reserve else YELLOW
        return _item("Keys.so", light, f"остаток суточной квоты {left} (резерв {reserve})",
                     "" if light == GREEN else "квота дня выбрана — прогон выдачи перенести")

    def gsc():
        from ..adapters.gsc import SearchConsole, service_account_token
        g = src["gsc"]
        token = service_account_token(g["key_file"])
        return _item("Search Console", GREEN, SearchConsole(g["site_url"], token, instance.today.isoformat(),
                                                            transport=instance.transport).probe())

    def webmaster():
        from ..adapters.webmaster import Webmaster
        w = src["webmaster"]
        return _item("Вебмастер", GREEN, Webmaster(w["user_id"], w["host_id"], secret(p, w["token_env"]),
                                                   instance.today.isoformat(), transport=instance.transport).probe())

    for name, enabled, fn, hint in (("Keys.so", src["keysso"]["enabled"], keysso, "sources.keysso"),
                                    ("Search Console", src["gsc"]["enabled"], gsc, "sources.gsc"),
                                    ("Вебмастер", src["webmaster"]["enabled"], webmaster, "sources.webmaster")):
        items.append(_probe(name, fn) if enabled else _item(name, OFF, "источник выключен", f"включить {hint}"))
    if src["demand_file"]["enabled"]:
        path = instance.root / src["demand_file"]["path"]
        items.append(_item("файл ядра", GREEN if path.is_file() else RED, str(path)))
    else:
        items.append(_item("файл ядра", OFF, "не используется", "sources.demand_file — своё ядро CSV"))
    value = src["value"]
    if value["kind"] == "file":
        path = instance.root / value["file"]
        items.append(_item("ценность страниц", GREEN if path.is_file() else RED,
                           f"ступень 1–2, файл {value['file']}" + ("" if path.is_file() else " — не найден"),
                           "" if path.is_file() else "выгрузить файл денег по страницам"))
    else:
        items.append(_item("ценность страниц", OFF if value["kind"] == "none" else YELLOW,
                           f"источник ценности: {value['kind']} — слой 4 не блокирует",
                           "sources.value.kind: file — деньги или заявки по страницам"))

    def corpus():
        pages, report = instance.corpus()
        light = GREEN if pages else RED
        return _item("корпус", light, f"страниц {len(pages)}, без разделов {len(report['without_sections'])}")

    items.append(_probe("корпус", corpus))
    items.append(_freshness(instance))
    items.append(_cache(instance))
    items.append(_stock(instance))
    return items


def _freshness(instance):
    path = instance.file("demand_map")
    if not path.is_file():
        return _item("карта спроса", OFF, "ещё не собрана", "demand collect → cluster → map build")
    built = store.read_json(path, "demand_map")["report"]["date"]
    age = (instance.today - date.fromisoformat(built)).days
    light = GREEN if age <= 7 else YELLOW if age <= 30 else RED
    return _item("карта спроса", light, f"собрана {built}, {age} дн. назад", "" if light == GREEN else "map build")


def _cache(instance):
    folder = instance.root / "cache" / "serp"
    files = list(folder.rglob("*.json")) if folder.is_dir() else []
    if not files:
        return _item("кэш выдачи", OFF, "пуст", "cluster или map build снимут выдачу")
    dates = sorted(json.loads(f.read_text(encoding="utf-8"))["as_of"] for f in files)
    days = instance.p["thresholds"]["serp_cache_days"]
    stale = sum(1 for d in dates if (instance.today - date.fromisoformat(d)).days > days)
    light = GREEN if not stale else YELLOW
    return _item("кэш выдачи", light, f"записей {len(files)}, старше {days} дн.: {stale}; самая старая {dates[0]}")


def _stock(instance):
    from . import queue as queue_mod
    if not queue_mod.load(instance):
        return _item("запас очереди", OFF, "очереди нет", "queue build → queue refill")
    report, code = queue_mod.status(instance)
    light = GREEN if not report["below_norm"] else YELLOW if code == 0 else RED
    return _item("запас очереди", light, f"{report['stock']} тем ≈ {report['days']} дн. (норма {report['norm']})",
                 "" if light == GREEN else "queue refill")


def render(items) -> str:
    marks = {GREEN: "●", YELLOW: "◐", RED: "✗", OFF: "○"}
    lines = ["Самопроверка Контент-машины"]
    for i in items:
        lines.append(f"  {marks[i['light']]} {i['item']}: {i['light']} — {i['detail']}"
                     + (f" (как подключить: {i['hint']})" if i["hint"] else ""))
    return "\n".join(lines)


def register(sub):
    from ..cli import add_profile
    p = sub.add_parser("check", help="самопроверка: светофор по профилю, секретам, источникам, корпусу, очереди")
    add_profile(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    items = run_check(instance)
    print(json.dumps(items, ensure_ascii=False, indent=1) if args.json else render(items))
    return 1 if any(i["light"] == RED for i in items) else 0
