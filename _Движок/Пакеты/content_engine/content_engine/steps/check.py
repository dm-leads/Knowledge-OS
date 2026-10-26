"""Самопроверка (Т14.1): `content-engine check` — светофор по каждому пункту, с причиной и подсказкой.

Пункты: профиль; секреты по именам; каждый источник отвечает пробой (Keys.so — остаток квоты, консоли — минимальный
запрос); корпус читается; свежесть карты спроса и кэша выдачи; запас очереди. Части 1 волны 2 (Т25.2): ступени прав
публикации, индексации и записи в базу; сайт отвечает на чтение; файл ключа IndexNow; браузер; контракт сборщика и
публикатора проекта (без записи); роли моделей; файлы инстанса, которые попали под правила игнорирования git. Цвет —
зелёный, жёлтый, красный или «не подключено». Отказ одного пункта не останавливает остальные: отозванный ключ —
красный пункт, а не падение.
"""
from __future__ import annotations

import json
from datetime import date

from ..core import store
from ..core.errors import AccessDenied, EngineError, QuotaExhausted, SourceError
from ..core.env_file import has_secret, secret

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
    items += wave2_items(instance)
    return items


def wave2_items(instance) -> list[dict]:
    """Пункты части 1 волны 2 (Т25.2) и сверка с правилами игнорирования git."""
    from ..core.profile import ROLES
    p = instance.p
    items = [_item("ступени прав", GREEN, f"публикация {p['publish']['allow']} ({p['publish']['mode']}), индексация "
                                          f"{p['indexing']['allow']}, база проекта {p['records']['allow']}",
                   "выше files — строкой профиля по разрешению владельца и записью в журнале действий")]

    def site():
        from ..adapters.page_fetch import PageFetcher
        url = f"https://{p['site']['domains'][0]}/"
        got = PageFetcher.for_own_site(instance).get(url, allow_redirects=True)
        return _item("сайт на чтение", GREEN if got.status == 200 else RED, f"{url} — HTTP {got.status}")

    items.append(_probe("сайт на чтение", site))
    ix = p["indexing"]["indexnow"]
    if not ix["enabled"]:
        items.append(_item("файл ключа IndexNow", OFF, "IndexNow выключен", "indexing.indexnow.enabled: true и "
                                                                           "файл ключа на сайте"))
    else:
        def key_file():
            from ..adapters.indexnow import key_file_ok
            ok, detail = key_file_ok(instance, secret(p, ix["key_env"]))
            return _item("файл ключа IndexNow", GREEN if ok else RED, detail)
        items.append(_probe("файл ключа IndexNow", key_file))
    from . import layout
    if not p["publish"]["browser"]:
        items.append(_item("браузер", OFF, "проверка вёрстки выключена", "publish.browser: true"))
    else:
        there = layout.available()
        items.append(_item("браузер", GREEN if there else YELLOW, "Playwright установлен" if there else
                           "Playwright не установлен — вёрстка «не подключено»", "" if there else
                           "pip install content_engine[browser]"))

    def assembler():
        from .assemble import contract
        problems = contract(instance)
        return _item("контракт сборщика", GREEN if not problems else RED,
                     f"{p['assemble']['adapter']}: " + ("выполнен" if not problems else "; ".join(problems[:3])))

    items.append(_probe("контракт сборщика", assembler))
    items.append(_publisher(instance))
    items.append(_item("роли моделей", GREEN, ", ".join(f"{r}: {p['models'][r]['primary']} / "
                                                        f"{p['models'][r]['fallback']}" for r in ROLES)))
    items.append(_ignored(instance))
    return items


def _publisher(instance):
    from ..publishers.base import load_publisher
    if instance.p["publish"]["adapter"] == "none":
        return _item("контракт публикатора", OFF, f"публикатора нет (режим {instance.p['publish']['mode']})",
                     "publish.adapter: static_repo, cms_api или module:… — для прямой публикации")
    import tempfile
    from pathlib import Path
    from ..assemblers.base import write_manifest
    try:
        publisher = load_publisher(instance)
        missing = [m for m in ("plan", "read_back", "restore") if not callable(getattr(publisher, m, None))]
        if missing:
            return _item("контракт публикатора", RED, f"нет методов {missing}")
        with tempfile.TemporaryDirectory() as tmp:          # план на синтетическом пакете — ни одной записи
            folder = Path(tmp)
            (folder / "article.html").write_text("<html><body><h1>Проверка</h1></body></html>", encoding="utf-8")
            domain = instance.p["site"]["domains"][0]
            package = write_manifest(folder, {"id": "check", "built": instance.today.isoformat(), "title": "Проверка",
                                              "description": "", "slug": "proverka-publikatora", "h1": "Проверка",
                                              "canonical": f"https://{domain}/proverka-publikatora",
                                              "main": "article.html", "blocks": ["article.html"]}, ["article.html"])
            planned = publisher.plan(package)
    except (EngineError, OSError) as exc:
        return _item("контракт публикатора", RED, str(exc))
    kinds = ", ".join(a.kind for a in planned)
    return _item("контракт публикатора", GREEN, f"{instance.p['publish']['adapter']}: план на синтетическом пакете — "
                                                f"{kinds or 'пусто'} (без записи)")


def _ignored(instance):
    """Т42.4 частично: профиль, реестры, известные ответы, адаптеры и паспорт выпуска не должны игнорироваться."""
    from pathlib import Path
    from ..core.ignore import Ignore
    import content_engine
    root = instance.root
    checker = Ignore(root)
    if checker.root is None:
        return _item("правила игнорирования", OFF, "инстанс не в репозитории git")
    wanted = [instance.profile_path, *root.glob("known_answers*.yaml"), *(root / "adapters").glob("*.py")]
    wanted += [instance.file(n) for n in ("queue", "reservations", "rejections", "whitelist", "frozen", "bets",
                                          "publications", "spend") if instance.file(n).is_file()]
    passport = Path(content_engine.__file__).parent / "_паспорт_выпуска.json"
    if passport.is_file():
        wanted.append(passport)
    hit = []
    for path in wanted:
        ignored, why = checker.ignored(path)
        if ignored:
            hit.append(f"{path.relative_to(checker.root).as_posix()} — {why}")
    note = " (.git/info/exclude и глобальный core.excludesFile не сверяются)"
    if hit:
        return _item("правила игнорирования", YELLOW, f"под правила игнорирования попали {len(hit)}: "
                     + "; ".join(hit[:5]) + note, "дописать исключения «!…» в .gitignore")
    return _item("правила игнорирования", GREEN, f"проверено файлов {len(wanted)}, игнорируемых нет" + note)


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
