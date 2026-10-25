"""`content-engine profile check` — проверка профиля и печать шаблона (Т2)."""
from __future__ import annotations

from ..core import profile


def register(sub):
    from ..cli import add_profile
    parser = sub.add_parser("profile", help="профиль проекта: check | template")
    actions = parser.add_subparsers(dest="action", required=True)
    check = actions.add_parser("check", help="проверить профиль")
    add_profile(check)
    check.set_defaults(func=run_check)
    template = actions.add_parser("template", help="напечатать шаблон профиля с комментариями")
    template.set_defaults(func=run_template)


def run_check(args) -> int:
    data = profile.load(args.profile)
    sources = [name for name in ("gsc", "webmaster", "keysso", "demand_file") if data["sources"][name]["enabled"]]
    print(f"профиль в порядке: домен {data['site']['domains'][0]}, папок корпуса {len(data['corpus']['folders'])}, "
          f"источники спроса: {', '.join(sources) or 'не подключены'}")
    return 0


def run_template(args) -> int:
    print(profile.template_text())
    return 0
