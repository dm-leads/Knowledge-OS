"""Касание (этап 7 методологии): ответ автору вопроса в личку и сбор его ответов. Выключено по умолчанию.

Правила в коде, а не в промпте:
- работает только при `outreach.enabled: true` в профиле; без флага --send — сухой прогон (кому и что ушло бы);
- пишет только автору конкретного сообщения по ссылке из очереди, которую одобрил человек; базы ников не ведёт;
- текст — ровно из очереди; без фразы «кто пишет» (outreach.required_phrase) и длиннее max_chars — не уходит;
- одному человеку — один раз (журнал отправок); кто попросил не писать — в стоп-лист навсегда;
- окно часов, лимит новых людей в сутки, случайная пауза между сообщениями; вопрос старше max_age_days — пропуск;
- стоп-сигнал аккаунта — стоп прогона и запись в stops.jsonl; свежий стоп номера (24 ч) — отправка не начинается.
Очередь, журнал, ответы и стоп-лист — в private_dir/outreach (вне git): там номера пользователей Telegram.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import hashlib
import json
import random
import re
from pathlib import Path

from lead_catcher.accounts import Account, last_stop
from lead_catcher.profile import Profile, ProfileError
from lead_catcher.redact import clean
from lead_catcher.stops import MAX_WAIT_S, classify, stop

LINK_RX = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,})/(\d+)/?$")
SEND_STATUS = "отправить"
DRAFT_STATUS = "черновик"   # виден только в сухом прогоне
NOT_DELIVERED = ("UserPrivacyRestrictedError", "UserIsBlockedError", "InputUserDeactivatedError",
                 "PeerIdInvalidError", "YouBlockedUserError", "PRIVACY_PREMIUM_REQUIRED")
OPT_OUT_RX = re.compile(r"(?i)не\s*пиш|спам|отстан|не\s*беспоко|отпиш|удалите|заблокир|не\s*надо\s*мне\s*пис")
STOP_FRESH_H = 24


def outreach_dir(p: Profile) -> Path:
    from lead_catcher.onboard import _ignored, _in_git

    if p.private_dir is None:
        raise ProfileError("в профиле нет private_dir — журнал отправок и номера пользователей хранить некуда "
                           "(нужна папка вне git)")
    d = p.private_dir / "outreach"
    d.mkdir(parents=True, exist_ok=True)
    repo = _in_git(d.resolve())
    if repo is not None and not _ignored(repo, d.resolve() / "probe.jsonl"):
        raise ProfileError(f"private_dir {p.private_dir} внутри git-репозитория {repo} и не закрыта .gitignore")
    return d


def _cfg(p: Profile):
    if p.outreach is None:
        raise ProfileError("отправка выключена: в профиле нет outreach.enabled: true (решение человека)")
    return p.outreach


# --- файлы -----------------------------------------------------------------------------------------------------

def read_queue(f: Path, statuses: tuple[str, ...] = (SEND_STATUS,)) -> list[dict]:
    """CSV «;»: ссылка;текст;статус. Отправка берёт только «отправить»; сухой прогон показывает и «черновик»."""
    with f.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    need = {"ссылка", "текст", "статус"}
    if rows and not need <= set(rows[0]):
        raise ProfileError(f"очередь {f.name}: нужны столбцы {', '.join(sorted(need))}")
    return [r for r in rows if (r.get("статус") or "").strip().lower() in statuses]


def _jsonl(f: Path) -> list[dict]:
    if not f.is_file():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _append(f: Path, rec: dict) -> None:
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def journal(d: Path) -> list[dict]:
    return _jsonl(d / "журнал-отправок.jsonl")


def blocked(d: Path) -> set[int]:
    f = d / "не-писать.txt"
    if not f.is_file():
        return set()
    return {int(x) for x in re.findall(r"^\s*(\d+)", f.read_text(encoding="utf-8"), re.M)}


def add_blocked(d: Path, user_id: int, why: str) -> None:
    with (d / "не-писать.txt").open("a", encoding="utf-8") as fh:
        fh.write(f"{user_id}  # {dt.date.today().isoformat()} {why}\n")


def text_problem(o, text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return "пустой текст"
    if o.required_phrase.lower() not in t.lower():
        return "нет фразы «кто пишет»"
    if len(t) > o.max_chars:
        return f"длиннее {o.max_chars} знаков"
    if re.search(r"https?://|t\.me/|www\.", t):
        return "ссылка в первом сообщении"
    return None


def local_now(o, now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(dt.timezone(dt.timedelta(hours=o.utc_offset_hours)))


def in_window(o, now: dt.datetime | None = None) -> bool:
    return o.window[0] <= local_now(o, now).hour < o.window[1]


def sent_today(rows: list[dict], account: int, o, now: dt.datetime | None = None) -> int:
    day = local_now(o, now).date().isoformat()
    return sum(1 for r in rows if r.get("исход") == "отправлено" and r.get("аккаунт") == account
               and str(r.get("время_местное", ""))[:10] == day)


def fresh_stop(p: Profile, account: int, now: dt.datetime | None = None) -> dict | None:
    st = last_stop(p, account)
    if not st:
        return None
    try:
        t = dt.datetime.fromisoformat(st["time"])
    except (KeyError, ValueError):
        return st
    now = (now or dt.datetime.now()).replace(tzinfo=None)
    return st if now - t.replace(tzinfo=None) < dt.timedelta(hours=STOP_FRESH_H) else None


def _short(uid: int) -> str:
    return "…" + str(uid)[-3:]


# --- отправка --------------------------------------------------------------------------------------------------

async def _input_user(c, chat: str, m, sender):
    """Автор сообщения как адресат. «Неполный» пользователь из группы — через ссылку на его сообщение."""
    from telethon.tl.types import InputPeerUserFromMessage

    if getattr(sender, "min", False) or getattr(sender, "access_hash", None) is None:
        return InputPeerUserFromMessage(await c.get_input_entity(chat), m.id, sender.id)
    return await c.get_input_entity(sender)


async def reply(p: Profile, acc: Account, queue: Path, send: bool = False, now_fn=None) -> int:
    """Ответить авторам вопросов из очереди. Код выхода: 0 — прошло, 4 — стоп аккаунта, 2 — отказ до начала."""
    from telethon.errors import FloodWaitError, RPCError
    from telethon.tl.types import User

    from lead_catcher.collect import _connected

    o = _cfg(p)
    if acc.number != o.account:
        raise ProfileError(f"отправка закреплена за номером {o.account} (outreach.account), а не {acc.number}")
    d = outreach_dir(p)
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
    st = fresh_stop(p, acc.number)
    if st:
        print(f"ОТКАЗ: у номера {acc.number} стоп {st.get('time')}: {st.get('kind')} — отправку не начинаю. "
              "Возобновить — решение человека (после разбора).")
        return 2
    rows = read_queue(queue, (SEND_STATUS,) if send else (SEND_STATUS, DRAFT_STATUS))
    jr = journal(d)
    done_links = {r["ссылка"] for r in jr if r.get("исход") in ("отправлено",) or
                  str(r.get("исход", "")).startswith(("пропуск", "не доставлено"))}
    contacted = {r.get("user_id") for r in jr if r.get("исход") == "отправлено"}
    stop_list = blocked(d)
    mode = "ОТПРАВКА" if send else "сухой прогон (ничего не отправляется; отправить — флаг --send)"
    print(f"{mode}; номер {acc.number}; строк к разбору: {len(rows)}; лимит {o.per_day} в сутки, "
          f"окно {o.window[0]}:00–{o.window[1]}:00, пауза {o.interval_s[0] // 60}–{o.interval_s[1] // 60} мин")
    if send and not in_window(o, now_fn()):
        print(f"ОТКАЗ: сейчас {local_now(o, now_fn()):%H:%M} — вне окна отправки")
        return 2
    left = o.per_day - sent_today(jr, acc.number, o, now_fn())
    jf = d / "журнал-отправок.jsonl"
    written = sent = 0
    c = await _connected(acc)
    try:
        for i, r in enumerate(rows, 1):
            link, text = r["ссылка"].strip(), r["текст"].strip()
            def log(outcome: str, **extra):
                nonlocal written
                rec = {"время": now_fn().isoformat(timespec="seconds"),
                       "время_местное": local_now(o, now_fn()).isoformat(timespec="seconds"),
                       "аккаунт": acc.number, "ссылка": link, "исход": outcome,
                       "текст_хэш": hashlib.sha1(text.encode()).hexdigest()[:12], **extra}
                if send:
                    _append(jf, rec)
                    written += 1
                print(f"  {i}. {link.split('/')[-2]}/{link.split('/')[-1]}: {outcome}"
                      + (f" — житель {_short(extra['user_id'])}" if extra.get("user_id") else ""))
            if link in done_links:
                print(f"  {i}. уже обработано раньше — пропуск без записи")
                continue
            mt = LINK_RX.match(link)
            if not mt:
                log("пропуск: ссылка не вида t.me/<чат>/<номер>")
                continue
            prob = text_problem(o, text)
            if prob:
                log(f"пропуск: {prob}")
                continue
            if send and sent >= left:
                print(f"  лимит {o.per_day} в сутки выбран — остальное завтра")
                break
            chat, mid = mt.group(1), int(mt.group(2))
            try:
                m = await c.get_messages(chat, ids=mid)
                sender = await m.get_sender() if m else None
            except FloodWaitError as e:
                if e.seconds > MAX_WAIT_S:
                    raise stop(p, acc.number, classify(e) or f"ожидание {e.seconds} с", "FloodWaitError")
                await asyncio.sleep(e.seconds + 5)
                m = await c.get_messages(chat, ids=mid)
                sender = await m.get_sender() if m else None
            except RPCError as e:
                kind = classify(e)
                if kind:
                    raise stop(p, acc.number, kind, type(e).__name__)
                log(f"пропуск: чат или сообщение недоступны ({type(e).__name__})")
                continue
            if m is None:
                log("пропуск: сообщение удалено")
                continue
            age = (now_fn() - m.date).days
            if age > o.max_age_days:
                log(f"пропуск: вопросу {age} дн. (больше {o.max_age_days})")
                continue
            if not isinstance(sender, User):
                log("пропуск: автор не человек (канал или группа)")
                continue
            if sender.bot or sender.is_self or sender.deleted:
                log("пропуск: бот, удалённый аккаунт или мы сами")
                continue
            if sender.id in stop_list:
                log("пропуск: в списке «не писать»", user_id=sender.id)
                continue
            if sender.id in contacted:
                log("пропуск: этому жителю уже писали", user_id=sender.id)
                continue
            if not send:
                log("отправил бы" if r["статус"].strip().lower() == SEND_STATUS else "отправил бы после «отправить»",
                    user_id=sender.id)
                print("     текст: " + text)
                continue
            try:
                peer = await _input_user(c, chat, m, sender)
                out = await c.send_message(peer, text)
                back = await c.get_messages(peer, ids=out.id)
                ok = back is not None and (back.message or "").strip() == text
                log("отправлено" if ok else "отправлено, но не прочитано обратно",
                    user_id=sender.id, чат=chat, сообщение=mid, наше=out.id)
                if ok:
                    sent += 1
                    contacted.add(sender.id)
            except RPCError as e:
                kind = classify(e)
                if kind:
                    raise stop(p, acc.number, kind, type(e).__name__)
                name = type(e).__name__
                if any(x in name or x in str(e) for x in NOT_DELIVERED):
                    log(f"не доставлено: {name}", user_id=sender.id)
                    continue
                log(f"СТОП прогона: неизвестный отказ Telegram {name}", user_id=sender.id)
                print("  Отправка остановлена до разбора — неизвестный отказ может быть началом ограничения.")
                return 1
            if i < len(rows):
                w = random.uniform(*o.interval_s)
                print(f"     пауза {w / 60:.1f} мин")
                await asyncio.sleep(w)
                if not in_window(o, now_fn()):
                    print("  окно отправки закончилось — остальное завтра")
                    break
    finally:
        await c.disconnect()
    if send:
        back_n = len(journal(d)) - len(jr)
        print(f"отправлено {sent}; строк журнала записано {written}, прочитано обратно {back_n}; журнал: {jf}")
        return 0 if back_n == written else 1
    return 0


# --- ответы ----------------------------------------------------------------------------------------------------

async def inbox(p: Profile, acc: Account) -> int:
    """Ответы жителей на наши сообщения: новые входящие после нашего — в ответы.jsonl (private), сводка на экран
    обезличенно. «Не пишите», «спам» — в стоп-лист. Сообщения прочитанными не отмечаются."""
    from telethon.errors import RPCError
    from telethon.tl.types import InputPeerUserFromMessage

    from lead_catcher.collect import _connected

    _cfg(p)
    d = outreach_dir(p)
    sent = [r for r in journal(d) if r.get("исход") == "отправлено" and r.get("аккаунт") == acc.number]
    seen = {(r["user_id"], r["id"]) for r in _jsonl(d / "ответы.jsonl")}
    stop_list = blocked(d)
    af = d / "ответы.jsonl"
    new = 0
    c = await _connected(acc)
    try:
        for r in sent:
            uid = r["user_id"]
            try:
                try:
                    peer = await c.get_input_entity(uid)
                except (ValueError, TypeError):
                    peer = InputPeerUserFromMessage(await c.get_input_entity(r["чат"]), r["сообщение"], uid)
                msgs = await c.get_messages(peer, min_id=r["наше"], limit=30)
            except RPCError as e:
                kind = classify(e)
                if kind:
                    raise stop(p, acc.number, kind, type(e).__name__)
                print(f"  житель {_short(uid)}: не прочитать ({type(e).__name__})")
                continue
            inc = [m for m in reversed(msgs) if not m.out]
            if not inc:
                print(f"  житель {_short(uid)} ({r['ссылка'].split('/')[-2]}): ответа нет")
                continue
            for m in inc:
                if (uid, m.id) in seen:
                    continue
                txt = m.message or "[не текст]"
                opt = bool(OPT_OUT_RX.search(txt))
                _append(af, {"user_id": uid, "id": m.id, "дата": m.date.isoformat(timespec="seconds"),
                             "ссылка": r["ссылка"], "текст": txt, "просит_не_писать": opt})
                new += 1
                if opt and uid not in stop_list:
                    add_blocked(d, uid, "попросил не писать")
                    stop_list.add(uid)
            last = inc[-1]
            flag = " — ПРОСИТ НЕ ПИСАТЬ, в стоп-листе" if uid in stop_list else ""
            print(f"  житель {_short(uid)} ({r['ссылка'].split('/')[-2]}): ответов {len(inc)}, последний "
                  f"{last.date:%d.%m %H:%M}: {clean(last.message or '[не текст]')[:200]}{flag}")
    finally:
        await c.disconnect()
    print(f"новых ответов записано: {new}; файл: {af}")
    return 0
