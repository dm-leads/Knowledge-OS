"""Аккаунты по номерам: ключи, сессия, прокси; проверка ключей без входа, проверка сессии, вход.

Номер = пара ключей приложения (api_id/api_hash) + своя сессия + свой прокси (необязательно). Сессии одного номера
с ключами другого не смешиваются. Значения ключей, номер телефона, код и пароль не печатаются.
"""
from __future__ import annotations

import getpass
import json
import os
from dataclasses import dataclass, field
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
    owner: str = ""            # папка экземпляра — кто держит замок сессии
    device: dict = field(default_factory=dict)  # как клиент представляется Telegram (--desktop)

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
    return Account(n, int(api_id), api_hash, p.sessions_dir / spec.session, label, net, str(p.root))


def client(acc: Account, memory: bool = False):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    sess = StringSession() if memory else str(acc.session_path)
    return TelegramClient(sess, acc.api_id, acc.api_hash, flood_sleep_threshold=60, **acc.net, **acc.device)


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
    from lead_catcher.lock import SessionBusy, SessionLock
    try:
        lock = SessionLock(acc.session_file, acc.owner).acquire()
    except SessionBusy as e:
        return f"занята: {e}"
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
        lock.release()


async def login(acc: Account) -> str:
    """Вход по номеру: телефон, код и облачный пароль вводит человек в терминале; ничего не сохраняется,
    кроме файла сессии (секрет, вне git)."""
    from lead_catcher.lock import SessionLock

    acc.session_path.parent.mkdir(parents=True, exist_ok=True)
    lock = SessionLock(acc.session_file, acc.owner).acquire()
    c = client(acc)
    await c.connect()
    try:
        if not await c.is_user_authorized():
            phone = normalize_phone(input(f"Номер телефона аккаунта №{acc.number} (+7…): "))
            print(f"Запрашиваю код для номера …{phone[-2:]}")
            sent = await c.send_code_request(phone)
            await _enter_code(c, phone, sent)
        me = await c.get_me()
        return f"вход выполнен: номер {acc.number}, аккаунт id …{str(me.id)[-3:]}; сессия {acc.session_file} — секрет"
    finally:
        await c.disconnect()
        lock.release()
        if os.name != "nt" and acc.session_file.exists():
            os.chmod(acc.session_file, 0o600)


CODE_ROUTES = {
    "SentCodeTypeApp": "в приложение Telegram этого аккаунта — служебный чат «Telegram» на телефоне или в Desktop, "
                       "НЕ по SMS",
    "SentCodeTypeSms": "по SMS",
    "SentCodeTypeFirebaseSms": "по SMS",
    "SentCodeTypeSmsWord": "по SMS (слово)",
    "SentCodeTypeSmsPhrase": "по SMS (фраза)",
    "SentCodeTypeCall": "голосовым звонком",
    "SentCodeTypeFlashCall": "сброшенным звонком: код — последние цифры номера звонящего",
    "SentCodeTypeMissedCall": "пропущенным звонком: код — последние цифры номера звонящего",
    "SentCodeTypeEmailCode": "на почту, привязанную к аккаунту",
    "SentCodeTypeFragmentSms": "через Fragment (анонимный номер)",
    "SentCodeTypeSetUpEmailRequired": "никуда: Telegram требует сначала привязать почту — войдите в официальном "
                                      "приложении и привяжите почту в настройках",
}


def normalize_phone(raw: str) -> str:
    """Российский номер с «8» в начале, без «+» или с пробелами и скобками → «+7» и 10 цифр."""
    d = "".join(ch for ch in raw if ch.isdigit())
    if len(d) == 11 and d[0] == "8":
        d = "7" + d[1:]
    if len(d) < 10:
        raise ProfileError("номер телефона слишком короткий — нужен полный номер с кодом страны (+7…)")
    return "+" + d


def describe_code(sent) -> str:
    """Куда Telegram отправил код и что будет при повторной просьбе (без самого кода и номера)."""
    name = type(getattr(sent, "type", None)).__name__
    text = f"Код отправлен {CODE_ROUTES.get(name, f'способом {name}')}."
    nxt = getattr(sent, "next_type", None)
    if nxt is not None:
        n2 = type(nxt).__name__.replace("CodeType", "SentCodeType")
        how = CODE_ROUTES.get(n2, n2).split(":")[0]
        wait = getattr(sent, "timeout", None)
        text += f" Не пришёл — нажмите Enter без кода, запрошу другим способом ({how})"
        text += f", Telegram разрешит через {wait} с." if wait else "."
    else:
        text += " Другого способа Telegram не предлагает."
    return text


async def _enter_code(c, phone: str, sent) -> None:
    from telethon.errors import (PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError)
    from telethon.tl.functions.auth import ResendCodeRequest

    print(describe_code(sent))
    for _ in range(6):
        code = input("Код (Enter без кода — прислать другим способом): ").strip()
        if not code:
            if getattr(sent, "next_type", None) is None:
                print("Другого способа нет. Проверьте чат «Telegram» в приложении этого аккаунта.")
                continue
            try:
                sent = await c(ResendCodeRequest(phone_number=phone, phone_code_hash=sent.phone_code_hash))
            except Exception as e:  # FloodWait, SEND_CODE_UNAVAILABLE и т. п. — человеку, без повтора
                print(f"Telegram не прислал код другим способом: {type(e).__name__}. Подождите и повторите вход.")
                raise
            print(describe_code(sent))
            continue
        try:
            await c.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            return
        except PhoneCodeInvalidError:
            print("Код неверный, введите ещё раз.")
        except PhoneCodeExpiredError:
            raise ProfileError("код устарел — запустите вход заново")
        except SessionPasswordNeededError:
            await c.sign_in(password=getpass.getpass("Облачный пароль (двухэтапная проверка): "))
            return
    raise ProfileError("вход не завершён: слишком много попыток")


# --- Вход в два шага (агент запрашивает код, человек передаёт код из приложения) -------------------------------
# Состояние между шагами — файл <сессия>.pending рядом с сессией (папка секретов, вне git): номер телефона и
# phone_code_hash. Облачный пароль — только интерактивно у человека (--password), в аргументы и чат не идёт.

def _pending_file(acc: Account) -> Path:
    return acc.session_path.with_name(acc.session_path.name + ".pending")


def desktop_identity() -> dict:
    """Параметры клиента как у Telegram Desktop на этом компьютере: имя ПК, Windows x64, версия Desktop, язык ru.
    По умолчанию Telethon называет себя «AMD64», приложение — версией Telethon, язык — en; для свежего аккаунта
    это похоже на бота. 21.11.2026 код входа с умолчаниями Telegram «отправлял», но не доставлял."""
    import platform
    u = platform.uname()
    system = f"Windows {u.release} x64" if u.system == "Windows" else f"{u.system} {u.release}"
    return {"device_model": u.node or "Desktop", "system_version": system, "app_version": "5.16.4 x64",
            "lang_code": "ru", "system_lang_code": "ru-RU"}


def with_overrides(p: Profile, acc: Account, keys_from: int | None, direct: bool,
                   desktop: bool = False) -> Account:
    """Диагностика входа: ключи другого номера (--keys M), без прокси (--direct), клиент как Telegram Desktop
    (--desktop). Сессия остаётся своя."""
    from dataclasses import replace
    if desktop:
        acc = replace(acc, device=desktop_identity())
    if keys_from is not None:
        other = resolve(p, keys_from)
        acc = replace(acc, api_id=other.api_id, api_hash=other.api_hash)
    if direct:
        acc = replace(acc, net={}, net_label="напрямую")
    return acc


def _as_requested(acc: Account, pend: dict) -> Account:
    """Шаги входа после запроса кода идут тем же клиентом, каким код запрошен (--desktop из запроса)."""
    from dataclasses import replace
    if pend.get("desktop") and not acc.device:
        acc = replace(acc, device=desktop_identity())
    return acc


async def send_code_step(acc: Account, phone: str, note: dict) -> str:
    from lead_catcher.lock import SessionLock

    phone = normalize_phone(phone)
    acc.session_path.parent.mkdir(parents=True, exist_ok=True)
    lock = SessionLock(acc.session_file, acc.owner).acquire()
    c = client(acc)
    await c.connect()
    try:
        if await c.is_user_authorized():
            return "вход уже выполнен — код не нужен"
        sent = await c.send_code_request(phone)
        _pending_file(acc).write_text(json.dumps({"phone": phone, "hash": sent.phone_code_hash, **note}),
                                      encoding="utf-8")
        return describe_code(sent) + f" Дальше: login {acc.number} --code <код> (те же --keys/--direct/--desktop)."
    finally:
        await c.disconnect()
        lock.release()


async def resend_code_step(acc: Account) -> str:
    from telethon.tl.functions.auth import ResendCodeRequest
    from lead_catcher.lock import SessionLock

    pend = json.loads(_pending_file(acc).read_text(encoding="utf-8"))
    acc = _as_requested(acc, pend)
    lock = SessionLock(acc.session_file, acc.owner).acquire()
    c = client(acc)
    await c.connect()
    try:
        sent = await c(ResendCodeRequest(phone_number=pend["phone"], phone_code_hash=pend["hash"]))
        pend["hash"] = sent.phone_code_hash
        _pending_file(acc).write_text(json.dumps(pend), encoding="utf-8")
        return describe_code(sent)
    finally:
        await c.disconnect()
        lock.release()


async def code_step(acc: Account, code: str | None, password: bool = False) -> str:
    from telethon.errors import SessionPasswordNeededError
    from lead_catcher.lock import SessionLock

    pf = _pending_file(acc)
    if not pf.is_file() and not password:   # пароль после входа по QR — без запроса кода
        raise ProfileError(f"нет запроса кода для №{acc.number}: сначала login {acc.number} --phone <номер>")
    pend = json.loads(pf.read_text(encoding="utf-8")) if pf.is_file() else {}
    acc = _as_requested(acc, pend)
    lock = SessionLock(acc.session_file, acc.owner).acquire()
    c = client(acc)
    await c.connect()
    try:
        try:
            if password:
                await c.sign_in(password=getpass.getpass("Облачный пароль (двухэтапная проверка): "))
            else:
                await c.sign_in(phone=pend["phone"], code=code.strip(), phone_code_hash=pend["hash"])
        except SessionPasswordNeededError:
            return (f"код принят, нужен облачный пароль: человек запускает в своём терминале "
                    f"login {acc.number} --password (те же --keys/--direct)")
        me = await c.get_me()
        if pf.is_file():
            pf.unlink()
        return f"вход выполнен: номер {acc.number}, аккаунт id …{str(me.id)[-3:]}; сессия {acc.session_file} — секрет"
    finally:
        await c.disconnect()
        lock.release()


async def qr_login(acc: Account, total_s: int = 240) -> str:
    """Вход по QR без кода: человек сканирует QR в Telegram на телефоне, где аккаунт уже открыт
    (Настройки → Устройства → Подключить устройство). Нужен, когда Telegram «отправляет» код в приложение, но не
    доставляет (21.11.2026: неофициальная программа + прокси + свежий аккаунт). QR — секрет на ~30 с: картинка в папке
    сессий (вне git), удаляется после входа или по истечении."""
    import asyncio
    import time

    import qrcode
    import qrcode.image.svg
    from telethon.errors import SessionPasswordNeededError

    from lead_catcher.lock import SessionLock

    img = acc.session_path.with_name(acc.session_path.name + ".qr.svg")
    acc.session_path.parent.mkdir(parents=True, exist_ok=True)
    lock = SessionLock(acc.session_file, acc.owner).acquire()
    c = client(acc)
    await c.connect()
    try:
        if await c.is_user_authorized():
            return "вход уже выполнен"
        qr = await c.qr_login()
        end = time.monotonic() + total_s
        shown = False
        while time.monotonic() < end:
            qrcode.make(qr.url, image_factory=qrcode.image.svg.SvgFillImage, box_size=20).save(str(img))
            if not shown:
                print("QR открыт в браузере. Телефон: Telegram этого аккаунта → Настройки → Устройства → "
                      "«Подключить устройство» → наведите камеру. QR обновляется сам, окно не закрывайте.")
                if os.name == "nt":
                    os.startfile(str(img))  # noqa: S606 — открыть картинку человеку
                shown = True
            else:
                print("QR обновлён — обновите страницу в браузере (F5).")
            try:
                await qr.wait(timeout=max(5, min(25, end - time.monotonic())))
                break
            except asyncio.TimeoutError:
                await qr.recreate()
            except SessionPasswordNeededError:
                return (f"QR принят, нужен облачный пароль: запустите login {acc.number} --password "
                        f"(или login.cmd {acc.number} --password) и введите пароль сами")
        else:
            return "время вышло — QR не отсканирован; запустите вход по QR заново"
        me = await c.get_me()
        return f"вход выполнен: номер {acc.number}, аккаунт id …{str(me.id)[-3:]}; сессия {acc.session_file} — секрет"
    finally:
        try:
            img.unlink()
        except OSError:
            pass
        await c.disconnect()
        lock.release()


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
