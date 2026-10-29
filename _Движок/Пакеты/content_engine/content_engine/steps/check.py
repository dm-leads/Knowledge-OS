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
from pathlib import Path

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
        serp = instance.serp()
        left = serp.remaining()
        if left >= 10 ** 9:                      # ответил запасной источник: суточной квоты нет, предел — деньги
            return _item("Keys.so", YELLOW, f"Keys.so не ответил — выдача из запасного источника "
                         f"({getattr(serp, 'last_source', '') or 'yandex_search'}), платно через бюджет-гейт",
                         "проверить ключ и подписку Keys.so")
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
    items.append(_stale_packages(instance))
    items.append(_growth(instance))
    items += part2_items(instance)
    items += wave3_items(instance)
    if "refresh" in instance.p:                 # профиль версии 5
        items += wave3b_items(instance)
    return items


def wave3_items(instance) -> list[dict]:
    """Пункты волны 3 (Т42.4): позиции, сводка, деньги по окну, чек-лист аудита, расписание и последний запуск,
    свежесть пульса и аудита. Без источника — «не подключено» с заданием, как подключить."""
    import json as _json
    from ..core import audit_store
    p, items = instance.p, []
    src = p["sources"]
    engines = [n for n, on in (("Google — Search Console", src["gsc"]["enabled"]),
                               ("Яндекс — Вебмастер", src["webmaster"]["enabled"])) if on]
    if p["monitor"]["positions"] == "none" or not engines:
        items.append(_item("позиции пульса", OFF, "консоли не подключены", "sources.gsc, sources.webmaster"))
    else:
        items.append(_item("позиции пульса", GREEN, ", ".join(engines)))
    n = p["notify"]
    items.append(_item("сводка пульса", OFF if n["kind"] == "none" else GREEN,
                       "не подключена — панель только файлом" if n["kind"] == "none"
                       else f"{n['kind']}, переменная {n['url_env']}; notify.allow {n['allow']}",
                       "notify.kind: lark_webhook и notify.url_env; отправка — notify.allow: write" if n["kind"] == "none" else ""))
    m = p["bets"]["money"]
    if m["kind"] == "none":
        items.append(_item("деньги за окно ставки", OFF, "замер ставки даст «нет данных»",
                           "bets.money: file (CSV с периодом на строку) или module:… адаптер выгрузки"))
    elif m["kind"] == "file":
        path = Path(m["file"]) if Path(m["file"]).is_absolute() else instance.root / m["file"]
        items.append(_item("деньги за окно ставки", GREEN if path.is_file() else RED, str(path)))
    else:
        items.append(_item("деньги за окно ставки", GREEN, f"адаптер {m['kind']}"))
    from .audit import load_checklist
    try:
        checks = load_checklist(instance)
        items.append(_item("технический чек-лист", GREEN if checks else OFF,
                           f"проверок {len(checks)}" if checks else "нет — аудит недели не подключён",
                           "" if checks else "`content-engine audit checklist --apply`"))
    except EngineError as exc:
        items.append(_item("технический чек-лист", RED, str(exc)))
    snap = audit_store.latest(instance)
    age = audit_store.age_days(instance, snap)
    stale = p["monitor"]["audit_stale_days"]
    items.append(_item("свежесть аудита", OFF if snap is None else GREEN if age <= stale else YELLOW,
                       "аудита ещё не было" if snap is None else f"{snap['date']}, {age} дн. назад",
                       "`content-engine audit run`" if snap is None or age > stale else ""))
    pulse_dir = instance.root / "data" / "pulse"
    last = sorted(pulse_dir.glob("????-??-??.json")) if pulse_dir.is_dir() else []
    if last:
        v = _json.loads(last[-1].read_text(encoding="utf-8"))["verdict"]
        items.append(_item("пульс", GREEN, f"{last[-1].stem}: {v['color']} — {v['why']}"))
    else:
        items.append(_item("пульс", OFF, "пульса ещё не было", "`content-engine pulse run`"))
    runs = store.read_jsonl(instance.root / "data" / "schedule_runs.jsonl", missing_ok=True)
    jobs = p["schedule"]["jobs"]
    if not jobs:
        items.append(_item("расписание", OFF, "заданий в профиле нет", "schedule.jobs и `schedule install`"))
    else:
        tail = "; ".join(f"{j['name']}: " + next((f"{r['start']} код {r['code']}" for r in reversed(runs)
                                                   if r.get("command") == j["command"]), "запусков нет")
                          for j in jobs)
        last_codes = [next((r["code"] for r in reversed(runs) if r.get("command") == j["command"]), None)
                      for j in jobs]
        ok = runs and all(c == 0 for c in last_codes if c is not None)
        items.append(_item("расписание", GREEN if ok else YELLOW,
                           f"заданий {len(jobs)} (schedule.allow {p['schedule']['allow']}); последний запуск — {tail}",
                           "" if runs else "`schedule install --apply` по разрешению; запуски пишутся в "
                                         "data/schedule_runs.jsonl"))
    return items


def wave3b_items(instance) -> list[dict]:
    """Пункты части 2 волны 3: цитирование в ответах ИИ, поведение, история спроса, конкуренты, «поле шире»."""
    p, items = instance.p, []
    a = p["ai_visibility"]
    if a["citations"] == "none":
        items.append(_item("цитирование в ответах ИИ", OFF, "не подключено — балл пригодности работает без него",
                           "ai_visibility.citations: keysso и ai_visibility.max_quota > 0"))
    else:
        quota = a["max_quota"]
        items.append(_item("цитирование в ответах ИИ", GREEN if quota > 0 else YELLOW,
                           f"{a['citations']}, предел квоты {quota}",
                           "" if quota > 0 else "ai_visibility.max_quota > 0 — иначе запрос не уходит"))
    b = p["behavior"]
    if b["kind"] == "none":
        items.append(_item("поведение на странице", OFF, "опция не подключена",
                           "behavior.kind: metrika, behavior.token_env и counter_env"))
    else:
        try:
            secret(p, b["token_env"]), secret(p, b["counter_env"])
            items.append(_item("поведение на странице", GREEN, f"{b['kind']}, воронка {len(b['funnel'])} шагов"))
        except EngineError as exc:
            items.append(_item("поведение на странице", RED, str(exc)))
    rows = store.read_jsonl(instance.root / "data" / "demand_history.jsonl", missing_ok=True)
    items.append(_item("история спроса для заморозки", GREEN if rows else OFF,
                       f"фраз {len({r['phrase'] for r in rows})}, последний съём "
                       f"{max(r['as_of'] for r in rows)}" if rows else "ряда по месяцам нет",
                       "" if rows else "`freeze scan --fetch --confirm-spend <₽>` (облако Wordstat, цена до запроса)"))
    comps = p["search"]["competitors"]
    items.append(_item("разведка конкурентов", GREEN if comps else OFF,
                       f"доменов {len(comps)}" if comps else "доменов нет",
                       "" if comps else "search.competitors"))
    items.append(_item("поле шире освоенного", GREEN if p["field"]["file"] else OFF,
                       p["field"]["file"] or "файла тем новых направлений нет",
                       "" if p["field"]["file"] else "field.file — «направление;тема;формулировка»"))
    return items


def part2_items(instance) -> list[dict]:
    """Пункты части 2 (Т33): роли текста и вычитки, вторая модель на вычитке, судья, картинки, бюджет, спрос, папка
    выпущенных статей."""
    p = instance.p
    items = []
    fc = p["models"]["fact_checker"]["primary"]
    critic = p["models"]["critic"]["primary"]
    writer = p["models"]["writer"]["primary"]
    if p["facts"]["mode"] == "human":
        items.append(_item("вычитка фактов", GREEN, "вычитывает человек (facts.mode: human)"))
    elif fc == writer:
        items.append(_item("вычитка фактов", YELLOW, f"вычитка той же моделью, что автор ({fc})",
                           "models.fact_checker — вторая модель (codex-latest) или facts.mode: human"))
    else:
        items.append(_item("вычитка фактов", GREEN, f"вычитка — {fc}, автор — {writer}, критик — {critic}"))
    emb = p["judge"]["embeddings"]
    items.append(_item("судья", GREEN, f"пятёрка разделов: {'эмбеддинги API' if emb['kind'] == 'api' else 'лексическая'}"
                                       f", роль judge — {p['models']['judge']['primary']}"))
    im = p["images"]
    paid = [k for k, v in im["providers"].items() if v.startswith("module:")]
    limits = p["budget"]["run_limit_rub"], p["budget"]["month_limit_rub"]
    light = GREEN if not paid or limits[0] > 0 else YELLOW
    items.append(_item("картинки", light, "провайдеры: " + ", ".join(f"{k} — {v}" for k, v in im["providers"].items())
                       + f"; загрузка: {im['upload']} (images.allow {im['allow']})"
                       + (f"; платные вызовы запрещены профилем (потолки {limits[0]}/{limits[1]} ₽)"
                          if paid and limits[0] <= 0 else ""),
                       "budget.run_limit_rub и month_limit_rub — потолки на картинки" if light != GREEN else ""))
    src = p["sources"]
    demand = [n for n in ("client_questions", "suggest") if src[n]["enabled"]]
    demand += ["wordstat (" + "→".join(src["wordstat"]["order"]) + ")"] if src["wordstat"]["order"] else []
    items.append(_item("спрос части 2", GREEN if demand else OFF, ", ".join(demand) or "вопросы клиентов, Wordstat и "
                       "подсказки не подключены", "" if demand else "sources.client_questions, wordstat, suggest"))
    items.append(_item("источники выдачи", GREEN, " → ".join(src["serp_order"])))
    if p["paths"]["published_dir"]:
        base = p["paths"]["published_dir"].replace("\\", "/").split("{")[0].rstrip("/")
        there = (instance.root / base).is_dir()
        items.append(_item("папка выпущенных статей", GREEN if there else RED,
                           f"{instance.root / base} — " + ("есть" if there else "нет") +
                           f"; передача — paths.allow {p['paths']['allow']}"))
    else:
        items.append(_item("папка выпущенных статей", OFF, "статью в корпус проекта кладёт человек",
                           "paths.published_dir и paths.allow: write по разрешению"))
    return items


def _growth(instance):
    """Т26: связь с реестром гипотез; `require_bet: true` без неё — запас останется нулём."""
    from pathlib import Path
    g = instance.p["sources"]["growth"]
    if g["kind"] == "none":
        if instance.p["queue"]["require_bet"]:
            return _item("ставки из гипотез", YELLOW, "sources.growth выключен, а queue.require_bet: true — темы без "
                         "ставки «спорно», запас очереди останется нулём", "sources.growth или require_bet: false")
        return _item("ставки из гипотез", OFF, "источник гипотез не подключён — ставка вручную `queue bet`",
                     "sources.growth.kind: file (карточки) или markdown (хранилище Движка роста)")
    folder = Path(g["path"])
    folder = folder if folder.is_absolute() else instance.root / folder
    if not folder.is_dir():
        return _item("ставки из гипотез", RED, f"папки {folder} нет", "sources.growth.path")
    found = sorted(folder.glob("*.yaml" if g["kind"] == "file" else "Гипотезы.md"))
    return _item("ставки из гипотез", GREEN if found else YELLOW,
                 f"{g['kind']}: {folder} — " + (f"карточек {len(found)}" if g["kind"] == "file" else
                                                ("лист гипотез есть" if found else "листа гипотез нет")))


def _stale_packages(instance):
    """Н3: пакет готов больше суток, а «доехало» не проверено — день выкладки запишется днём проверки."""
    import json
    from datetime import date
    from ..core import publications
    stale = []
    root = instance.topics_root()
    for manifest in sorted(root.glob("*/package/manifest.json")) if root.is_dir() else []:
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tid = manifest.parent.parent.name
        built = str(m.get("built") or "")
        if m.get("ready") and built and (instance.today - date.fromisoformat(built)).days >= 1 \
                and publications.published(instance, tid=tid) is None:
            stale.append(f"{tid} (собран {built})")
    if not stale:
        return _item("пакеты без «доехало»", GREEN, "готовых пакетов старше суток без проверки нет")
    return _item("пакеты без «доехало»", YELLOW, "готовы, но не проверены: " + ", ".join(stale),
                 "после вставки — `publish verify` в тот же день или `--inserted-on ГГГГ-ММ-ДД --who <имя>`")


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
    checklist = root / instance.p["audit"]["checklist"]
    if checklist.is_file():
        wanted.append(checklist)                     # технический чек-лист проекта — решение проекта, в git
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
    p.add_argument("--contours", action="store_true", help="приёмка на контур: признак работы каждого контура (Т42.5)")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    if args.contours:
        instance = open_instance(args, read_only=True)
        from ..core import contours
        rows = contours.table(instance)
        mods = contours.modules(instance)
        if args.json:
            print(json.dumps({"contours": rows, "modules": mods}, ensure_ascii=False, indent=1))
        else:
            print(contours.render(rows))
            print(f"\nМодули карты: в коде {len(mods['built'])} из {mods['total']}"
                  + (f"; нет в коде: {', '.join(mods['missing'])}" if mods["missing"] else ""))
            for m, why in mods["not_connected"].items():
                print(f"  не подключено у проекта — {m}: {why}")
        return 0
    instance = open_instance(args)
    items = run_check(instance)
    print(json.dumps(items, ensure_ascii=False, indent=1) if args.json else render(items))
    return 1 if any(i["light"] == RED for i in items) else 0
