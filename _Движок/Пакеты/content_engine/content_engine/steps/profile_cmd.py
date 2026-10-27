"""`content-engine profile check | template | migrate` — проверка профиля, шаблон, перевод на версию вверх (Т2, Т15)."""
from __future__ import annotations

from ..core import migrate, profile


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("profile", help="профиль проекта: check | template | migrate")
    actions = parser.add_subparsers(dest="action", required=True)
    check = actions.add_parser("check", help="проверить профиль")
    add_profile(check)
    check.set_defaults(func=run_check)
    template = actions.add_parser("template", help="напечатать шаблон профиля с комментариями")
    template.set_defaults(func=run_template)
    move = actions.add_parser("migrate", help="перевести профиль на версию вверх: без --apply — только план")
    add_profile(move)
    move.add_argument("--apply", action="store_true", help="записать перевод и прочитать профиль обратно")
    move.set_defaults(func=run_migrate)


def run_check(args) -> int:
    data = profile.load(args.profile)
    sources = [name for name in ("gsc", "webmaster", "keysso", "demand_file") if data["sources"][name]["enabled"]]
    print(f"профиль в порядке: версия {data['schema_version']}, домен {data['site']['domains'][0]}, папок корпуса "
          f"{len(data['corpus']['folders'])}, источники спроса: {', '.join(sources) or 'не подключены'}")
    return 0


def run_template(args) -> int:
    print(profile.template_text())
    return 0


def run_migrate(args) -> int:
    step = migrate.plan(args.profile)
    print(f"Перевод профиля: версия {step.version_from} → {step.version_to}")
    for line in step.changes:
        print(f"  + {line}")
    if not args.apply:
        print("план: файл не изменён; записать — с --apply")
        return 0
    print(migrate.apply(args.profile, step))
    if step.version_to < profile.SCHEMA_VERSION:
        print(f"профиль версии {step.version_to}, пакет ждёт {profile.SCHEMA_VERSION}: повторите `profile migrate` — "
              "перевод идёт на одну версию за запуск")
    return 0
