"""Публикация (Г1, Т21), «доехало» (Г2, Т22), записи (Г4, Т23) и откат (Т16.5): `content-engine publish …`.

- `prepare <id>` — ручной режим: пакет для вставки человеком (блоки по порядку, мета, разметка, нативные блоки,
  картинки с alt, `paste.md`, `rollback.md`);
- `apply <id> [--apply]` — прямой режим: блокировка → финальная чистка → снимок «до» → план → запись с `--apply`;
- `next` — следующая тема с готовым пакетом, если по ритму пора и сейчас окно публикации;
- `verify <id> [--url]` — «доехало» по ответу сайта; успех сразу делает записи дня выкладки;
- `record <id>` — записи дня выкладки повтором (без дублей);
- `rollback <id> | --run <run_id> [--apply]` — возврат из снимков «до» тем же адаптером.
Запреты в коде: без утверждённого брифа — ни пакета, ни публикации; без ступени прав — действие не в плане; живая
блокировка — второй прогон не идёт; нет снимка «до» — записи нет.
"""
from __future__ import annotations

from ..core import actions as actions_mod
from ..core import approval, locks, publications, rights, snapshots
from ..core.errors import EngineError, GateRefused, RightsRefused, SnapshotMissing


def package_of(instance, tid: str, *, ready: bool = False):
    """Пакет сборки темы; `ready` — запрет публиковать пакет, который не прошёл проверки уровня block (Т20.4)."""
    from ..assemblers.base import Package
    package = Package.load(instance.brief_dir(tid) / "package")
    if ready and package.manifest.get("ready") is False:
        raise GateRefused(f"тема {tid}: сборка не готова (провал автопроверки уровня block) — `assemble build {tid}`")
    if ready:
        folder = instance.brief_dir(tid)
        for field, path in (("brief_sha256", folder / "brief.yaml"), ("article_sha256", folder / "article.md")):
            want = package.manifest.get(field)
            if not want or not path.is_file() or approval.digest(path) != want:
                raise GateRefused(f"тема {tid}: пакет собран из другого {path.name} — `assemble build {tid}` заново")
    return package


def rollback(instance, tid: str | None, run_id: str | None, apply: bool, transport=None) -> int:
    """Откат по виду снимка (З36): передача статьи и загрузка картинок — своими восстановителями, и в ручном режиме;
    публикатору — только его снимки; страницу, вставленную человеком, снимает человек по `rollback.md`."""
    from ..core import restore
    from ..publishers.base import load_publisher
    publisher = load_publisher(instance, transport)
    manual = instance.brief_dir(tid, create=False) / "package" / "rollback.md" if tid else None
    try:
        snaps = snapshots.load(instance, tid=None if run_id else tid, run_id=run_id)
    except SnapshotMissing:
        if publisher is not None:
            raise
        snaps = []
    own, rest = restore.split(snaps)
    steps, by_hand = restore.actions(instance, own)
    if rest and publisher is not None:
        steps += publisher.restore(rest)
    elif rest:
        by_hand = [f"откатить вручную: {s['action']} → {s['target']}" for s in rest]
    if publisher is None and not steps:
        for line in by_hand:
            print(f"  {line}")
        if manual and manual.is_file():
            print("Ручной режим: страницу снимает человек по инструкции пакета\n")
            print(manual.read_text(encoding="utf-8"))
            return 0
        raise EngineError("ручной режим без публикатора: откат — инструкция пакета `rollback.md` "
                          "(сначала `publish prepare <id>`)")
    allowed, refused = rights.split(instance, steps)
    for line in refused:
        print(f"  не в плане — {line}")
    if apply and not allowed:
        raise RightsRefused("откат не выполнен: ни одно действие не проходит ступень прав")
    owner = tid or snaps[0]["id"]
    with locks.held(instance, owner, "rollback"):
        out = actions_mod.run(instance, owner, allowed, apply=apply,
                              run_id=snapshots.new_run_id(instance, snapshots.ROLLBACK) if apply else None)
    if apply:
        publications.log(instance, "rollback", id=owner, run=run_id or snaps[0]["run_id"], written=out.written,
                         confirmed=out.confirmed)
    for line in by_hand:
        print(f"  {line}")
    if publisher is None and manual and manual.is_file():
        print("\nСтраницу снимает человек по инструкции пакета:\n")
        print(manual.read_text(encoding="utf-8"))
    return 0


def apply_publish(instance, tid: str, apply: bool, transport=None) -> int:
    from ..publishers.base import load_publisher
    publisher = load_publisher(instance, transport)
    if instance.p["publish"]["mode"] == "manual" or publisher is None:
        raise GateRefused(f"ручной режим (publish.mode: {instance.p['publish']['mode']}): `publish prepare {tid}` → "
                          f"вставка человеком → `publish verify {tid}`")
    with locks.held(instance, tid, "publish"):
        # проверки — под блокировкой: между ними и записью бриф, текст и пакет не меняются другим прогоном
        approval.require(instance, tid)
        package = package_of(instance, tid, ready=True)
        problems = final_clean(instance, tid, package, transport=transport)
        if problems:
            for line in problems:
                print(f"  ✗ {line}")
            raise GateRefused(f"финальная чистка не прошла: {len(problems)} — публикация не начата")
        planned = publisher.plan(package)
        allowed, refused = rights.split(instance, planned)
        for line in refused:
            print(f"  не в плане — {line}")
        if apply and not allowed:
            raise RightsRefused(f"ни одно действие не проходит ступень прав (publish.allow = "
                                f"{rights.level(instance, 'publish')})")
        out = actions_mod.run(instance, tid, allowed, apply=apply)
    if apply and out.confirmed:
        publications.log(instance, "apply", id=tid, run=out.run_id, written=out.written, confirmed=out.confirmed,
                         actions=[a.kind for a in allowed])
        if any(a.right == ("publish", "publish") for a in allowed):
            print(f"дальше: `publish verify {tid}` — «доехало» по ответу сайта и записи дня выкладки")
    return 0


def prepare_manual(instance, tid: str) -> dict:
    """Т21.2: всё для ручной вставки — блоки по порядку, мета, разметка, нативные блоки, картинки с alt, `paste.md`,
    `rollback.md`. Файлы входят в манифест пакета."""
    from ..assemblers.base import write_manifest
    approval.require(instance, tid)
    package = package_of(instance, tid, ready=True)
    problems = final_clean(instance, tid, package)
    if problems:
        for line in problems:
            print(f"  ✗ {line}")
        raise GateRefused(f"финальная чистка не прошла: {len(problems)} — пакет для вставки не готовится")
    m = package.manifest
    native = m.get("native_blocks") or []
    images = m.get("images") or []
    lines = [f"# Вставка статьи {tid}: {m['h1']}", "",
             f"Итоговый адрес: {m['canonical']}", "",
             "## Мета страницы",
             f"- Title: {m['title']}", f"- Description: {m['description']}", f"- Slug: {m['slug']}",
             f"- Разметка: `schema.json` ({m.get('schema_format', 'json-ld')})" if "schema.json" in m["files"]
             else "- Разметка: в блоках страницы", "",
             "## Блоки — по порядку вставки"]
    lines += [f"{n}. `{name}`" for n, name in enumerate(m["blocks"], 1)]
    lines += ["", "## Нативные блоки конструктора — ставит человек"]
    lines += [f"- {b}" for b in native] or ["- нет"]
    lines += ["", "## Картинки"]
    lines += [f"- {img.get('url') or img.get('file')}: alt «{img.get('alt', '')}»" for img in images] or \
        ["- в блоках страницы (alt — в разметке блоков)"]
    notes = m.get("notes") or []
    if notes:
        lines += ["", "## Пояснения сборки — в код страницы не вставлять",
                  "Комментарии сняты из блоков (Н5: публикуемый HTML без комментариев); их текст — для справки."]
        lines += [f"- `{n['file']}`: {n['comment']}" for n in notes[:40]]
    lines += ["", "## После вставки — в тот же день",
              f"`content-engine publish verify {tid} --url {m['canonical']} --profile <профиль>` — «доехало» по "
              "ответу сайта и записи дня выкладки. День выкладки — день первого «доехало»: запустите в день вставки "
              f"или укажите его: `--inserted-on ГГГГ-ММ-ДД --who <имя>`."]
    rollback_lines = [f"# Снять статью {tid} вручную", "",
                      f"1. Открыть страницу {m['canonical']} в редакторе сайта.",
                      "2. Снять страницу с публикации (или вернуть прежние блоки, если страница была).",
                      f"3. Проверить: адрес {m['canonical']} не отвечает 200 или показывает прежнюю версию.",
                      "4. Записать в журнал действий проекта: что снято и почему."]
    (package.folder / "paste.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (package.folder / "rollback.md").write_text("\n".join(rollback_lines) + "\n", encoding="utf-8",
                                                newline="\n")
    meta = {k: v for k, v in m.items() if k not in ("files", "schema_version", "kind")}
    files = [f for f in m["files"] if f not in ("paste.md", "rollback.md")] + ["paste.md", "rollback.md"]
    package = write_manifest(package.folder, meta, files)
    return {"folder": str(package.folder), "url": m["canonical"], "blocks": m["blocks"]}


def next_topic(instance) -> dict:
    """Т21.6: следующая тема с готовым пакетом — если по ритму пора и сейчас окно публикации."""
    from datetime import date, time, timedelta
    from . import queue as queue_mod
    q, pub = instance.p["queue"], instance.p["publish"]
    last = publications.last_publication_day(instance)
    now = instance.now()
    if q["per_day"] <= 0:
        return {"id": None, "why": "queue.per_day = 0 — ритма нет"}
    if q["per_day"] >= 1:                    # несколько статей в день — счёт выкладок за сегодня
        today = sum(1 for r in publications.read(instance) if r["event"] == "published"
                    and r.get("day") == instance.today.isoformat())
        if today >= int(q["per_day"]):
            return {"id": None, "why": f"по ритму рано: сегодня уже {today} при ритме {q['per_day']} в день"}
        due = instance.today
    else:
        due = date.fromisoformat(last) + timedelta(days=round(1 / q["per_day"])) if last else instance.today
    ahead = timedelta(days=pub["schedule_ahead_days"])
    if instance.today + ahead < due:
        return {"id": None, "why": f"по ритму рано: последняя выкладка {last}, следующая — {due.isoformat()}"}
    start, end = (time.fromisoformat(x) for x in pub["window"])
    if not pub["schedule_ahead_days"] and not start <= now.time() <= end:
        return {"id": None, "why": f"вне окна публикации {pub['window'][0]}–{pub['window'][1]}"}
    done = {row.get("id") for row in publications.read(instance) if row["event"] == "published"}
    for topic in sorted(queue_mod.load(instance), key=lambda t: queue_mod.priority_key(instance, t)):
        if topic["status"] != queue_mod.TAKEN or topic["id"] in done:
            continue
        if not (instance.brief_dir(topic["id"]) / "package" / "manifest.json").is_file():
            continue
        try:
            package_of(instance, topic["id"], ready=True)
            approval.require(instance, topic["id"])
        except EngineError:
            continue
        how = (f"publish apply {topic['id']} --apply" if pub["mode"] == "direct"
               else f"publish prepare {topic['id']} → вставка человеком → publish verify {topic['id']}")
        return {"id": topic["id"], "why": f"по ритму пора (срок {due.isoformat()})", "how": how}
    return {"id": None, "why": "готовых пакетов нет"}


def final_clean(instance, tid: str, package, transport=None) -> list[str]:
    """Т21.5: автор и даты на месте, внутренние ссылки отвечают 200, «Читайте также» — только из брифа."""
    from .final_clean import check
    return check(instance, tid, package, transport=transport)


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("publish", help="публикация: prepare | apply | next | verify | record | rollback")
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("rollback", help="откат из снимков «до»: без --apply — план")
    add_profile(p)
    p.add_argument("id", nargs="?", help="id темы")
    p.add_argument("--run", help="id прогона: откатить все записи прогона")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("apply", help="прямая публикация: без --apply — план")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=run)
    p = actions.add_parser("prepare", help="ручной режим: пакет для вставки человеком (paste.md, rollback.md)")
    add_profile(p)
    p.add_argument("id")
    p.set_defaults(func=run)
    p = actions.add_parser("next", help="следующая тема с готовым пакетом по ритму и окну")
    add_profile(p)
    p.set_defaults(func=run)
    p = actions.add_parser("verify", help="«доехало» по ответу сайта; успех — записи дня выкладки")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--url", help="адрес страницы; без него — поиск по slug в карте сайта")
    p.add_argument("--no-browser", action="store_true", help="не проверять вёрстку браузером")
    p.add_argument("--apply", action="store_true", help="записи в базу проекта и передача статьи в папку проекта "
                                                         "(по ступеням records.allow и paths.allow)")
    p.add_argument("--inserted-on", help="день вставки страницы ГГГГ-ММ-ДД, если проверка идёт позже (Н3)")
    p.add_argument("--who", help="кто вставил страницу — со слов кого записан день вставки")
    p.set_defaults(func=run)
    p = actions.add_parser("record", help="записи дня выкладки повтором (без дублей)")
    add_profile(p)
    p.add_argument("id")
    p.add_argument("--apply", action="store_true", help="запись в базу проекта (по ступени records.allow)")
    p.set_defaults(func=run)


def run(args) -> int:
    from ..cli import open_instance
    instance = open_instance(args)
    if args.action == "rollback":
        if not args.id and not args.run:
            raise EngineError("нужен id темы или --run <id прогона>")
        return rollback(instance, args.id, args.run, args.apply)
    if args.action == "apply":
        return apply_publish(instance, args.id, args.apply)
    if args.action == "prepare":
        out = prepare_manual(instance, args.id)
        print(f"Пакет для вставки: {out['folder']} — блоки {', '.join(out['blocks'])}; адрес {out['url']}; "
              "инструкции: paste.md, rollback.md")
        print(f"После вставки запустите `publish verify {args.id}` в тот же день или укажите день вставки "
              "`--inserted-on ГГГГ-ММ-ДД --who <имя>` (Н3)")
        return 0
    if args.action == "next":
        out = next_topic(instance)
        print(f"Следующая: {out['id']} — {out['why']}; дальше: {out['how']}" if out["id"]
              else f"Ничего: {out['why']}")
        return 0
    if args.action == "verify":
        from .verify import run_verify
        if bool(args.inserted_on) != bool(args.who):
            raise EngineError("--inserted-on и --who задаются вместе: день вставки — со слов человека")
        return run_verify(instance, args.id, args.url, browser=not args.no_browser, apply=args.apply,
                          inserted_on=args.inserted_on, who=args.who)
    if args.action == "record":
        from .records import run_records
        return run_records(instance, args.id, apply=args.apply)
    raise EngineError(f"неизвестное действие {args.action}")
