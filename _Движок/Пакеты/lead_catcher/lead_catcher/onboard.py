"""Подключение проекта: папка экземпляра с profile.yaml, allowed_chats.txt, data/ и .gitignore.

Сессии и файл секретов — вне git: onboard отказывается, если папка сессий лежит внутри репозитория.
"""
from __future__ import annotations

from pathlib import Path

PROFILE_TEMPLATE = """\
# Экземпляр ловца лидов (пакет канона lead_catcher). Здесь — только имена переменных и пути, без значений.
# Пути: абсолютные или относительно этой папки. Секреты и сессии — вне git.
schema: 1
secrets_env: "{env_file}"
sessions_dir: "{sessions_dir}"
# Прокси по порядку: первый заданный (в файле секретов или в окружении) выигрывает; у аккаунта может быть свой proxy.
proxy_env: [{proxy_env}]
# Какой номер читает сейчас. Меняет человек: `use N`. Код номер сам не переключает.
active: {active}
accounts:
{accounts}
data_dir: "data"
allowed_chats: "allowed_chats.txt"
pace:
  seconds_between_requests: 3
  messages_per_day_to_model: 500
"""

ALLOWED_TEMPLATE = """\
# Чаты, которые разрешено читать (по одному: @адрес или ссылка t.me/…). Пополняет человек после карты чатов.
# Чтение и поиск по чату не из списка код отклоняет.
"""

GITIGNORE = """\
# Сессии Telegram — секрет (доступ к аккаунту без кода); здесь их быть не должно, но на всякий случай.
*.session
*.session-journal
.env
*.env
"""


def _in_git(path: Path) -> Path | None:
    for d in [path, *path.parents]:
        if (d / ".git").exists():
            return d
    return None


def onboard(instance_dir: Path, secrets_env: str, sessions_dir: str, accounts: list[tuple[int, str, str]],
            proxy_env: list[str]) -> list[Path]:
    instance_dir = instance_dir.resolve()
    if (instance_dir / "profile.yaml").exists():
        raise SystemExit(f"{instance_dir / 'profile.yaml'} уже есть — onboard не перезаписывает профиль")
    sess = Path(sessions_dir)
    sess = sess if sess.is_absolute() else instance_dir / sess
    repo = _in_git(sess.resolve())
    if repo is not None:
        raise SystemExit(f"папка сессий {sess} внутри git-репозитория {repo} — сессии должны лежать вне git")
    sec = Path(secrets_env)
    sec = sec if sec.is_absolute() else instance_dir / sec
    repo = _in_git(sec.resolve().parent)
    if repo is not None:
        raise SystemExit(f"файл секретов {sec} внутри git-репозитория {repo} — ключи должны лежать вне git")
    if not accounts:
        raise SystemExit("нужен хотя бы один аккаунт: --account N:ИМЯ_API_ID:ИМЯ_API_HASH")
    acc_lines = "\n".join(f"  {n}: {{api_id: {i}, api_hash: {h}, session: tg_reader_{n}, proxy: null}}"
                          for n, i, h in accounts)
    instance_dir.mkdir(parents=True, exist_ok=True)
    (instance_dir / "data").mkdir(exist_ok=True)
    sess.mkdir(parents=True, exist_ok=True)
    files = {
        "profile.yaml": PROFILE_TEMPLATE.format(env_file=secrets_env.replace("\\", "/"),
                                                sessions_dir=sessions_dir.replace("\\", "/"),
                                                proxy_env=", ".join(proxy_env), active=accounts[0][0],
                                                accounts=acc_lines),
        "allowed_chats.txt": ALLOWED_TEMPLATE,
        ".gitignore": GITIGNORE,
    }
    made = []
    for name, text in files.items():
        f = instance_dir / name
        if not f.exists():
            f.write_bytes(text.encode("utf-8"))
            made.append(f)
    keep = instance_dir / "data" / ".gitkeep"
    if not any((instance_dir / "data").iterdir()):
        keep.write_bytes(b"")
        made.append(keep)
    return made
