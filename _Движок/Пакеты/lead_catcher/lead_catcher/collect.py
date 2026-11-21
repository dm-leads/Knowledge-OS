"""Сбор, только чтение: карта чатов (этап 2), пробное чтение (этап 3), поиск по истории группы (этап 3, редкие
запросы за год). Без вступления в группы и без отправки чего-либо.

Тексты читаются и пишутся только по чатам из allowed_chats экземпляра (запрет в коде, не в промпте).
Исключение — карта чатов (find-chats): по найденным группам берёт последние 100 сообщений, чтобы посчитать живость;
сохраняет только даты, тексты и авторов не пишет и в память дальше функции не отдаёт (методология, этап 2).
Пишется только текст (обезличенный), дата, группа, ссылка; автор не запрашивается. Тексты для модели — под общим
дневным потолком (read-sample и search-groups вместе). Успех — записано = прочитано обратно и не ноль.
"""
from __future__ import annotations

import asyncio
import collections
import csv
import datetime as dt
import json
import random
from pathlib import Path

from lead_catcher.accounts import Account, client
from lead_catcher.profile import Profile, ProfileError, norm_chat, split_window
from lead_catcher.redact import clean
from lead_catcher.stops import MAX_WAIT_S, StopSignal, classify, stop

TEXT_FILES = ("выборка-*.jsonl", "поиск-в-группах-*.jsonl")


def guard_allowed(p: Profile, chats: list[str]) -> list[str]:
    allowed = p.allowed()
    names = [norm_chat(c) for c in chats]
    bad = [c for c in names if c not in allowed]
    if bad:
        raise ProfileError(f"чатов нет в {p.allowed_chats.name}: {', '.join('@' + b for b in bad)} — "
                           "добавить их в список — решение человека")
    return names


async def pause(p: Profile, k: float = 1.0) -> None:
    base = max(p.seconds_between_requests, 1.0) * k
    await asyncio.sleep(random.uniform(base, base * 2))


def out_dir(p: Profile) -> Path:
    d = p.data_dir / dt.date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_file(d: Path, stem: str, ext: str) -> Path:
    """Новый файл, не затирая прежний (два запуска в одну секунду)."""
    base = f"{stem}-{dt.datetime.now():%H%M%S}"
    f, i = d / f"{base}{ext}", 1
    while f.exists():
        f, i = d / f"{base}-{i}{ext}", i + 1
    return f


def day_left(p: Profile, d: Path) -> int:
    used = sum(1 for pat in TEXT_FILES for f in d.glob(pat) for _ in f.open(encoding="utf-8"))
    return p.messages_per_day - used


async def handle(p: Profile, acc: Account, e: BaseException) -> None:
    """Стоп-сигнал — остановить прогон. FloodWait до 5 минут — выждать, затем продолжать. Прочее — вызывающему."""
    from telethon.errors import FloodWaitError

    kind = classify(e)
    if kind:
        raise stop(p, acc.number, kind, type(e).__name__)
    if isinstance(e, FloodWaitError) and e.seconds <= MAX_WAIT_S:
        print(f"  пауза по просьбе Телеграма: {e.seconds} с")
        await asyncio.sleep(e.seconds + 5)


async def _connected(acc: Account):
    c = client(acc)
    await c.connect()
    if not await c.is_user_authorized():
        await c.disconnect()
        raise ProfileError(f"номер {acc.number}: нет входа — сначала `login {acc.number}`")
    return c


def _verify(out: Path, written: int, allow_zero: bool = False) -> int:
    back = sum(1 for _ in out.open(encoding="utf-8"))
    print(f"записано {written}, прочитано обратно {back}; файл: {out}")
    if back != written or (written == 0 and not allow_zero):
        return 1
    return 0


async def read_sample(p: Profile, acc: Account, groups: list[str], hours: int = 24, max_per_group: int = 300) -> int:
    pairs = [split_window(g) for g in groups]
    names = guard_allowed(p, [g for g, _ in pairs])
    windows = {n: (w or hours) for n, (_, w) in zip(names, pairs)}
    d = out_dir(p)
    left = day_left(p, d)
    if left <= 0:
        print(f"дневной потолок {p.messages_per_day} выбран — следующая порция завтра")
        return 0
    out = new_file(d, f"выборка-{hours}ч", ".jsonl")
    now = dt.datetime.now(dt.timezone.utc)
    written, seen, capped = 0, set(), False
    c = await _connected(acc)
    try:
        with out.open("w", encoding="utf-8") as f:
            for g in names:
                if capped:
                    break
                since = now - dt.timedelta(hours=windows[g])
                n_read = n_kept = 0
                try:
                    async for m in c.iter_messages(g, limit=max_per_group, wait_time=p.seconds_between_requests / 2):
                        if m.date < since:
                            break
                        n_read += 1
                        text = clean(m.message or "")
                        if not text or text in seen:
                            continue
                        if written + n_kept >= left:
                            print(f"дневной потолок: осталось {left}, дальше не читаю")
                            capped = True
                            break
                        seen.add(text)
                        f.write(json.dumps({"id": f"{g}/{m.id}", "state": text[:1500], "группа": "@" + g,
                                            "окно_ч": windows[g], "дата": m.date.strftime("%d.%m.%Y %H:%M"),
                                            "ссылка": f"https://t.me/{g}/{m.id}"}, ensure_ascii=False) + "\n")
                        n_kept += 1
                except StopSignal:
                    raise
                except Exception as e:
                    await handle(p, acc, e)
                    print(f"@{g}: прервано — {type(e).__name__}")
                print(f"@{g}: прочитано за {windows[g]} ч {n_read}, в выборку {n_kept}")
                written += n_kept
                await pause(p, 2)
    finally:
        await c.disconnect()
    return _verify(out, written)


async def search_groups(p: Profile, acc: Account, groups: list[str], words: list[str], days: int = 365,
                        limit: int = 30) -> int:
    names = guard_allowed(p, groups)
    d = out_dir(p)
    left = day_left(p, d)
    if left <= 0:
        print(f"дневной потолок {p.messages_per_day} выбран — следующая порция завтра")
        return 0
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    out = new_file(d, "поиск-в-группах", ".jsonl")
    written, seen, table, capped = 0, set(), collections.Counter(), False
    c = await _connected(acc)
    try:
        with out.open("w", encoding="utf-8") as f:
            for g in names:
                for w in words:
                    if capped:
                        break
                    n = 0
                    try:
                        async for m in c.iter_messages(g, search=w, limit=limit, wait_time=p.seconds_between_requests / 2):
                            if m.date < since:
                                break
                            text = clean(m.message or "")
                            if not text or text in seen:
                                continue
                            if written + n >= left:
                                print(f"дневной потолок: осталось {left}, дальше не ищу")
                                capped = True
                                break
                            seen.add(text)
                            f.write(json.dumps({"id": f"{g}/{m.id}", "state": text[:1500], "группа": "@" + g,
                                                "слово": w, "дата": m.date.strftime("%d.%m.%Y"),
                                                "ссылка": f"https://t.me/{g}/{m.id}"}, ensure_ascii=False) + "\n")
                            n += 1
                    except StopSignal:
                        raise
                    except Exception as e:
                        await handle(p, acc, e)
                        print(f"@{g} «{w}»: прервано — {type(e).__name__}")
                    table[(g, w)] = n
                    written += n
                    await pause(p, 1.5)
                if capped:
                    break
                await pause(p, 2)
    finally:
        await c.disconnect()
    for (g, w), n in table.items():
        print(f"  @{g} «{w}»: {n}")
    return _verify(out, written, allow_zero=True)


async def find_chats(p: Profile, acc: Account, words: list[str]) -> int:
    """Карта открытых групп по словам или по списку @адресов: название, адрес, число участников и живость (даты
    последних 100 сообщений). Тексты, имена и ники не сохраняются. В allowed_chats не пишет — решение человека."""
    from telethon import functions, types

    found: dict[int, dict] = {}
    c = await _connected(acc)

    async def call(req):
        for _ in range(2):
            try:
                return await c(req)
            except Exception as e:
                await handle(p, acc, e)  # стоп поднимет; короткое ожидание выждет — тогда повтор
                from telethon.errors import FloodWaitError
                if not isinstance(e, FloodWaitError):
                    raise
        return await c(req)

    try:
        for h in [w for w in words if w.startswith("@")]:
            try:
                res = await call(functions.contacts.ResolveUsernameRequest(username=h.lstrip("@")))
                ch = res.chats[0] if res.chats else None
            except StopSignal:
                raise
            except Exception as e:
                print(f"{h}: не найдено — {type(e).__name__}")
                await pause(p)
                continue
            if isinstance(ch, types.Channel) and ch.megagroup:
                found.setdefault(ch.id, {"группа": ch.title, "адрес": h, "участников": ch.participants_count or "",
                                         "найдена по": {"списку"}, "_e": ch})
            else:
                print(f"{h}: не открытая группа (канал или закрытый чат) — пропускаю")
            await pause(p)
        for w in [w for w in words if not w.startswith("@")]:
            res = await call(functions.contacts.SearchRequest(q=w, limit=30))
            n = 0
            for ch in res.chats:
                if not isinstance(ch, types.Channel) or not ch.megagroup or not ch.username:
                    continue
                n += 1
                row = found.setdefault(ch.id, {"группа": ch.title, "адрес": "@" + ch.username,
                                               "участников": ch.participants_count or "", "найдена по": set(),
                                               "_e": ch})
                row["найдена по"].add(w)
            print(f"«{w}»: открытых групп {n}")
            await pause(p, 1.5)
        now = dt.datetime.now(dt.timezone.utc)
        for row in found.values():
            try:
                hist = await call(functions.messages.GetHistoryRequest(
                    peer=row["_e"], offset_id=0, offset_date=None, add_offset=0, limit=100, max_id=0, min_id=0,
                    hash=0))
                dates = [m.date for m in hist.messages if getattr(m, "date", None)]
                del hist  # тексты и авторы дальше не идут
                row["сообщений за 7 дней"] = sum(1 for x in dates if (now - x).days < 7)
                row["последнее сообщение"] = max(dates).strftime("%d.%m.%Y") if dates else ""
                row["читается без вступления"] = "да"
            except StopSignal:
                raise
            except Exception as e:
                row["сообщений за 7 дней"] = ""
                row["последнее сообщение"] = ""
                row["читается без вступления"] = f"нет: {type(e).__name__}"
            await pause(p)
    finally:
        await c.disconnect()
    if not found:
        print("групп не найдено — карта не записана; смените слова")
        return 1
    out = new_file(out_dir(p), "карта чатов", ".csv")
    cols = ["группа", "адрес", "участников", "сообщений за 7 дней", "последнее сообщение",
            "читается без вступления", "найдена по"]
    rows = sorted(found.values(), key=lambda r: -(r.get("сообщений за 7 дней") or 0))
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f, delimiter=";")
        wr.writerow(cols)
        for r in rows:
            vals = [", ".join(sorted(r[k])) if k == "найдена по" else r.get(k, "") for k in cols]
            wr.writerow([" ".join(str(v).split()) for v in vals])  # без переводов строк в названиях
    with out.open(encoding="utf-8-sig", newline="") as f:
        back = sum(1 for _ in csv.reader(f, delimiter=";")) - 1
    print(f"групп найдено: {len(found)}; записано и прочитано обратно: {back}; файл: {out}")
    return 0 if back == len(found) else 1
