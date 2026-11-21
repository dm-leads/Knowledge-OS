"""Аккаунты по номерам: ключи, сессия, прокси; проверка ключей без входа, проверка сессии, вход.

Номер = пара ключей приложения (api_id/api_hash) + своя сессия + свой прокси (необязательно). Сессии одного номера
с ключами другого не смешиваются. Значения ключей, номер телефона, код и пароль не печатаются.
"""
from __future__ import annotations

import getpass
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from lead_catcher.profile import Profile, ProfileError


@dataclass
class Account:
    number: int
    api_id: int
    api_hash: str
    session_path: Path          # без расширения: Telethon добавит .session
    net_label: str
    net: dict

    @property
    def session_file(self) -> Path:
        return self.session_path.with_name(self.session_path.name + ".session")


def parse_proxy(name: str, url: str) -> tuple[str, dict]:
    """(подпись без логина и пароля, параметры TelegramClient)."""
    from telethon import connection

    u = urlsplit(url)
    if u.scheme == "tg" or (u.hostname in ("t.me", "telegram.me") and u.path.strip("/") == "proxy"):
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        server, port, secret = q.get("server"), q.get("port", ""), q.get("secret", "")
        if not server or not port.isdigit() or not secret:
            raise ProfileError(f"{name}: в ссылке MTProto-прокси нет server, port или secret")
        if secret.lower().startswith("ee"):
            raise ProfileError(f"{name}: секрет MTProto на «ee» Telethon не поддерживает — нужен socks5/http")
        return (f"{name}: MTProto {server}:{port}",
                {"connection": connection.ConnectionTcpMTProxyRandomizedIntermediate,
                 "proxy": (server, int(port), secret)})
    try:
        port = u.port
    except ValueError:
        port = None
    scheme = "socks5" if u.scheme == "socks5h" else u.scheme
    if scheme not in ("http", "socks5", "socks4") or not u.hostname or not port:
        raise ProfileError(f"{name}: не похож на http://, socks5:// или tg://proxy?… (или неверный порт)")
    return (f"{name}: {scheme} {u.hostname}:{port}",
            {"proxy": {"proxy_type": scheme, "addr": u.hostname, "port": port, "rdns": True,
                       "username": unquote(u.username) if u.username else None,
                       "password": unquote(u.password) if u.password else None}})


def resolve(p: Profile, number: int | None = None) -> Account:
    n = p.active if number is None else number
    spec = p.accounts.get(n)
    if spec is None:
        raise ProfileError(f"номера {n} нет в accounts (есть: {sorted(p.accounts)})")
    api_id, api_hash = p.lookup(spec.api_id_var), p.lookup(spec.api_hash_var)
    if not api_id.isdigit() or len(api_hash) != 32:
        raise ProfileError(f"номер {n}: нет {spec.api_id_var} (число) или {spec.api_hash_var} (32 знака) "
                           f"в файле секретов")
    label, net = "напрямую", {}
    for name in ([spec.proxy_var] if spec.proxy_var else []) + p.proxy_env:
        url = p.lookup(name)
        if url:
            label, net = parse_proxy(name, url)
            break
    return Account(n, int(api_id), api_hash, p.sessions_dir / spec.session, label, net)


def client(acc: Account, memory: bool = False):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    sess = StringSession() if memory else str(acc.session_path)
    return TelegramClient(sess, acc.api_id, acc.api_hash, flood_sleep_threshold=60, **acc.net)


async def probe_keys(acc: Account) -> str:
    """Ключи принимаются? Первый шаг входа по QR (auth.exportLoginToken) в памяти: токен не печатается,
    QR не строится, вход не происходит, код никому не уходит. help.getConfig ключи НЕ проверяет (21.11.2026)."""
    from telethon import functions
    from telethon.errors import ApiIdInvalidError

    c = client(acc, memory=True)
    try:
        await c.connect()
        await c(functions.auth.ExportLoginTokenRequest(api_id=acc.api_id, api_hash=acc.api_hash, except_ids=[]))
        return "приняты"
    except ApiIdInvalidError:
        return "ОТКАЗ: API_ID_INVALID"
    except Exception as e:  # сеть, прокси
        return f"не проверено: {type(e).__name__}"
    finally:
        await c.disconnect()


async def check_session(acc: Account, p: Profile | None = None) -> str:
    """Есть ли вход и жива ли сессия; номер телефона и имя не печатаются. Стоп-ошибка пишется в журнал стопов."""
    if not acc.session_file.is_file():
        return "входа нет"
    c = client(acc)
    try:
        await c.connect()
        if not await c.is_user_authorized():
            return "сессия завершена — нужен вход заново"
        me = await c.get_me()
        return f"жива (id …{str(me.id)[-3:]}{', бот' if me.bot else ''})"
    except Exception as e:
        from lead_catcher.stops import classify, record
        kind = classify(e)
        if kind and p is not None:
            record(p, acc.number, kind, type(e).__name__)
        return f"СТОП: {kind}" if kind else f"не проверено: {type(e).__name__}"
    finally:
        await c.disconnect()


async def login(acc: Account) -> str:
    """Вход по номеру: телефон, код и облачный пароль вводит человек в терминале; ничего не сохраняется,
    кроме файла сессии (секрет, вне git)."""
    from telethon.errors import SessionPasswordNeededError

    acc.session_path.parent.mkdir(parents=True, exist_ok=True)
    c = client(acc)
    await c.connect()
    try:
        if not await c.is_user_authorized():
            phone = input(f"Номер телефона аккаунта №{acc.number}, с кодом страны: ").strip()
            sent = await c.send_code_request(phone)
            code = input("Код из Телеграма (придёт в приложение этого аккаунта): ").strip()
            try:
                await c.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                await c.sign_in(password=getpass.getpass("Облачный пароль (двухэтапная проверка): "))
        me = await c.get_me()
        return f"вход выполнен: номер {acc.number}, аккаунт id …{str(me.id)[-3:]}; сессия {acc.session_file} — секрет"
    finally:
        await c.disconnect()
        if os.name != "nt" and acc.session_file.exists():
            os.chmod(acc.session_file, 0o600)


def last_stop(p: Profile, number: int) -> dict | None:
    f = p.data_dir / "stops.jsonl"
    if not f.is_file():
        return None
    last = None
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("account") == number:
            last = r
    return last
