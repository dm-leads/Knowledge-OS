"""Профиль экземпляра проекта (profile.yaml) и файл секретов.

В профиле — только имена переменных и пути; значения ключей читаются из файла секретов проекта и не печатаются.
Пути в профиле — абсолютные или относительно папки экземпляра.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SCHEMA = 1
PROFILE_NAME = "profile.yaml"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProfileError(SystemExit):
    """Ошибка профиля: сообщение для человека, код выхода 2."""

    def __init__(self, msg: str):
        super().__init__(f"профиль: {msg}")
        self.code = 2


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE построчно; комментарии и пустые строки пропускаются; кавычки снимаются."""
    out: dict[str, str] = {}
    if not path.is_file():
        raise ProfileError(f"нет файла секретов {path}")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        out[k] = v.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class AccountSpec:
    number: int
    api_id_var: str
    api_hash_var: str
    session: str
    proxy_var: str | None = None


@dataclass
class Profile:
    root: Path
    secrets_env: Path
    sessions_dir: Path
    data_dir: Path
    allowed_chats: Path
    questions: Path | None
    proxy_env: list[str]
    active: int
    accounts: dict[int, AccountSpec]
    seconds_between_requests: float = 3.0
    messages_per_day: int = 500
    private_dir: Path | None = None   # контакты администраторов и полные описания — вне git
    _env: dict[str, str] | None = field(default=None, repr=False)

    # --- секреты ---
    def env(self) -> dict[str, str]:
        if self._env is None:
            self._env = read_env_file(self.secrets_env)
        return self._env

    def lookup(self, name: str) -> str:
        """Значение переменной: сначала файл секретов проекта, затем окружение процесса."""
        return self.env().get(name) or os.environ.get(name, "")

    def secret_values(self) -> list[str]:
        """Все значения, которые нельзя печатать: ключи аккаунтов и адреса прокси с логином и паролем."""
        names: set[str] = set(self.proxy_env)
        for a in self.accounts.values():
            names.update({a.api_id_var, a.api_hash_var})
            if a.proxy_var:
                names.add(a.proxy_var)
        vals = []
        for n in names:
            v = self.lookup(n)
            if v:
                vals.append(v)
                from urllib.parse import parse_qs, unquote, urlsplit
                try:
                    u = urlsplit(v)
                    vals += [unquote(x) for x in (u.username, u.password) if x]
                    vals += [x for xs in parse_qs(u.query).get("secret", []) for x in [xs]]  # MTProto
                except ValueError:
                    pass
        return sorted({v for v in vals if len(v) >= 3}, key=len, reverse=True)

    def mask(self, text: str) -> str:
        for v in self.secret_values():
            text = text.replace(v, "***")
        return text

    # --- разрешённые чаты ---
    def allowed(self) -> set[str]:
        if not self.allowed_chats.is_file():
            return set()
        out = set()
        for line in self.allowed_chats.read_text(encoding="utf-8-sig").splitlines():
            s = line.split("#", 1)[0].strip()
            if s:
                out.add(norm_chat(s))
        return out


def split_window(s: str) -> tuple[str, int | None]:
    """«@чат:72» → ("@чат", 72); ссылки https://t.me/… с двоеточием схемы не режутся."""
    head, sep, tail = s.rpartition(":")
    if sep and tail.isdigit() and not head.endswith(("http", "https")):
        return head, int(tail)
    return s, None


def norm_chat(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^(?:https?://)?(?:t\.me|telegram\.me)/", "", s)
    return s.lstrip("@").split("/")[0].lower()


def _path(root: Path, v, what: str, required: bool = True) -> Path | None:
    if v in (None, ""):
        if required:
            raise ProfileError(f"нет поля {what}")
        return None
    p = Path(os.path.expandvars(os.path.expanduser(str(v))))
    return p if p.is_absolute() else (root / p)


def load(instance_dir: str | Path) -> Profile:
    root = Path(instance_dir).resolve()
    f = root / PROFILE_NAME
    if not f.is_file():
        raise ProfileError(f"нет {f} — подключите проект командой onboard")
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    if d.get("schema") != SCHEMA:
        raise ProfileError(f"schema {d.get('schema')!r}, пакет понимает {SCHEMA}")
    accs: dict[int, AccountSpec] = {}
    for k, v in (d.get("accounts") or {}).items():
        try:
            n = int(k)
        except (TypeError, ValueError):
            raise ProfileError(f"номер аккаунта {k!r} — не число")
        v = v or {}
        for fld in ("api_id", "api_hash"):
            if not _NAME.match(str(v.get(fld, ""))):
                raise ProfileError(f"аккаунт {n}: {fld} — имя переменной, не значение")
        sess = str(v.get("session") or f"tg_reader_{n}")
        if not re.fullmatch(r"[\w\-]+", sess):
            raise ProfileError(f"аккаунт {n}: session — имя файла без пути")
        pv = v.get("proxy")
        if pv is not None and not _NAME.match(str(pv)):
            raise ProfileError(f"аккаунт {n}: proxy — имя переменной")
        accs[n] = AccountSpec(n, v["api_id"], v["api_hash"], sess, pv)
    if not accs:
        raise ProfileError("нет ни одного аккаунта в accounts")
    sessions = [a.session for a in accs.values()]
    if len(set(sessions)) != len(sessions):
        raise ProfileError("у двух номеров одна сессия — у каждого номера своя")
    active = d.get("active")
    if active not in accs:
        raise ProfileError(f"active={active!r} — такого номера нет в accounts")
    pace = d.get("pace") or {}
    proxy_env = d.get("proxy_env") or []
    if not isinstance(proxy_env, list):
        raise ProfileError("proxy_env — список имён переменных в квадратных скобках")
    if any(not _NAME.match(str(x)) for x in proxy_env):
        raise ProfileError("proxy_env — список имён переменных")
    return Profile(
        root=root,
        secrets_env=_path(root, d.get("secrets_env"), "secrets_env"),
        sessions_dir=_path(root, d.get("sessions_dir"), "sessions_dir"),
        data_dir=_path(root, d.get("data_dir", "data"), "data_dir"),
        allowed_chats=_path(root, d.get("allowed_chats", "allowed_chats.txt"), "allowed_chats"),
        questions=_path(root, d.get("questions"), "questions", required=False),
        proxy_env=[str(x) for x in proxy_env],
        active=int(active),
        accounts=accs,
        seconds_between_requests=float(pace.get("seconds_between_requests", 3)),
        messages_per_day=int(pace.get("messages_per_day_to_model", 500)),
        private_dir=_path(root, d.get("private_dir"), "private_dir", required=False),
    )


def set_active(instance_dir: str | Path, number: int) -> None:
    """Меняет только строку active: в profile.yaml, сохраняя комментарии и переводы строк."""
    f = Path(instance_dir).resolve() / PROFILE_NAME
    raw = f.read_bytes().decode("utf-8")
    new, n = re.subn(r"(?m)^active:[^\r\n]*", f"active: {number}", raw, count=1)
    if n != 1:
        raise ProfileError("в профиле нет строки active:")
    f.write_bytes(new.encode("utf-8"))
