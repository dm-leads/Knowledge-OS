"""Команды ловца лидов. Запуск: uv run --project <пакет> python -m lead_catcher --instance <папка экземпляра> <команда>

  onboard --secrets-env P --sessions-dir P --account N:ИМЯ_ID:ИМЯ_HASH [--proxy-env ИМЯ …]   подключить проект
  status                     по каждому номеру: ключи (проба без входа), вход, сессия, последний стоп
  login N                    вход по номеру (телефон, код, пароль вводит человек в терминале)
  check [N]                  жива ли сессия номера (по умолчанию — активного)
  use N                      сделать номер активным (решение человека)
  find-chats СЛОВО|@адрес …  карта открытых групп: название, участники, живость (без текстов)
  chat-info @чат …           описание группы и видимые администраторы (контакты — в private_dir, вне git)
  read-sample @чат[:часы] …  [--hours 24 --max 300]   пробное чтение разрешённых чатов
  search-groups --groups @чат … --words слово …  [--days 365 --limit 30]   поиск по истории разрешённых чатов
  reply --queue ФАЙЛ [--send]  ответ автору вопроса в личку по одобренной очереди (только при outreach.enabled;
                             без --send — сухой прогон); номер — outreach.account
  inbox                      ответы жителей на наши сообщения — в private_dir, сводка обезличенно

Всё, что печатается после загрузки профиля, проходит маску секретов: значения ключей и логин/пароль прокси
заменяются на ***. Код выхода 4 — стоп-сигнал аккаунта (сбор остановлен, номер не переключён).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path

from lead_catcher import __version__


class _Masked(io.TextIOBase):
    def __init__(self, inner, mask):
        self.inner, self.mask = inner, mask

    def write(self, s):
        self.inner.write(self.mask(s))
        return len(s)

    def flush(self):
        self.inner.flush()


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lead_catcher", description=f"Ловец лидов {__version__} — чтение; ответы авторам — только при outreach.enabled")
    ap.add_argument("--instance", required=True, help="папка экземпляра проекта (с profile.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("onboard")
    o.add_argument("--secrets-env", required=True)
    o.add_argument("--sessions-dir", required=True)
    o.add_argument("--account", action="append", default=[], help="N:ИМЯ_API_ID:ИМЯ_API_HASH")
    o.add_argument("--proxy-env", nargs="*", default=["TG_READER_PROXY_URL"])
    sub.add_parser("status")
    lg = sub.add_parser("login")
    lg.add_argument("n", type=int)
    lg.add_argument("--phone", help="шаг 1 входа в два шага: запросить код (номер не печатается)")
    lg.add_argument("--code", help="шаг 2: код из приложения Telegram")
    lg.add_argument("--resend", action="store_true", help="запросить код другим способом")
    lg.add_argument("--qr", action="store_true", help="вход по QR без кода: сканировать в Telegram на телефоне")
    lg.add_argument("--password", action="store_true", help="облачный пароль — вводит человек в своём терминале")
    lg.add_argument("--keys", type=int, help="диагностика: ключи другого номера для этого входа")
    lg.add_argument("--direct", action="store_true", help="диагностика: без прокси")
    lg.add_argument("--desktop", action="store_true",
                    help="клиент представляется как Telegram Desktop (имя ПК, Windows, язык ru)")
    ck = sub.add_parser("check")
    ck.add_argument("n", type=int, nargs="?")
    u = sub.add_parser("use")
    u.add_argument("n", type=int)
    fc = sub.add_parser("find-chats")
    fc.add_argument("words", nargs="+")
    ci = sub.add_parser("chat-info")
    ci.add_argument("handles", nargs="+")
    rs = sub.add_parser("read-sample")
    rs.add_argument("groups", nargs="+")
    rs.add_argument("--hours", type=int, default=24)
    rs.add_argument("--max", type=int, default=300)
    sg = sub.add_parser("search-groups")
    sg.add_argument("--groups", nargs="+", required=True)
    sg.add_argument("--words", nargs="+", required=True)
    sg.add_argument("--days", type=int, default=365)
    sg.add_argument("--limit", type=int, default=30)
    rp = sub.add_parser("reply")
    rp.add_argument("--queue", required=True, help="CSV «;»: ссылка;текст;статус (берутся строки «отправить»)")
    rp.add_argument("--send", action="store_true", help="отправить по-настоящему; без флага — сухой прогон")
    sub.add_parser("inbox")
    return ap


async def _status(p) -> int:
    from lead_catcher.accounts import check_session, last_stop, probe_keys, resolve
    from lead_catcher.profile import ProfileError

    print(f"экземпляр: {p.root}; активный номер: {p.active}")
    for n in sorted(p.accounts):
        try:
            acc = resolve(p, n)
        except ProfileError as e:
            print(f"  №{n}: {e}")
            continue
        keys = await probe_keys(acc)
        sess = await check_session(acc, p)
        st = last_stop(p, n)
        mark = " (активный)" if n == p.active else ""
        print(f"  №{n}{mark}: ключи {keys}; сессия {sess}; сеть {acc.net_label}"
              + (f"; последний стоп {st['time']}: {st['kind']}" if st else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Любая ошибка — одной строкой на экран (через маску), а не молча: 21.11.2026 вход падал без текста,
    человек видел «код не пришёл» вместо причины."""
    from lead_catcher.profile import ProfileError
    try:
        return _run(argv)
    except ProfileError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 3
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # Telegram, сеть, прокси
        print(f"ОШИБКА Telegram/сети: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        for s in (sys.stdout, sys.stderr):
            try:
                s.flush()
            except Exception:
                pass


def _run(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    inst = Path(a.instance)
    if a.cmd == "onboard":
        from lead_catcher.onboard import onboard
        accs = []
        for s in a.account:
            parts = s.split(":")
            if len(parts) != 3 or not parts[0].isdigit():
                raise SystemExit(f"--account {s!r}: нужно N:ИМЯ_API_ID:ИМЯ_API_HASH")
            accs.append((int(parts[0]), parts[1], parts[2]))
        for f in onboard(inst, a.secrets_env, a.sessions_dir, accs, a.proxy_env):
            print("создан:", f)
        print("Дальше: status — проверка ключей; login N — вход (код вводит человек).")
        return 0

    from lead_catcher import profile as P
    p = P.load(inst)
    sys.stdout = _Masked(sys.stdout, p.mask)
    sys.stderr = _Masked(sys.stderr, p.mask)

    if a.cmd == "use":
        if a.n not in p.accounts:
            raise P.ProfileError(f"номера {a.n} нет в accounts")
        P.set_active(inst, a.n)
        print(f"активный номер: {a.n} (был {p.active})")
        return 0
    if a.cmd == "status":
        return asyncio.run(_status(p))

    from lead_catcher import accounts as A
    if a.cmd == "login":
        acc = A.with_overrides(p, A.resolve(p, a.n), a.keys, a.direct, a.desktop)
        keys = f"ключи №{a.keys}" if a.keys is not None else f"ключи №{a.n}"
        print(f"сеть: {acc.net_label}; {keys}" + ("; клиент как Telegram Desktop" if a.desktop else ""))
        if a.qr:
            print(asyncio.run(A.qr_login(acc)))
        elif a.phone:
            print(asyncio.run(A.send_code_step(acc, a.phone, {"keys": a.keys, "direct": a.direct, "desktop": a.desktop})))
        elif a.resend:
            print(asyncio.run(A.resend_code_step(acc)))
        elif a.code or a.password:
            print(asyncio.run(A.code_step(acc, a.code, password=a.password)))
        else:
            print(asyncio.run(A.login(acc)))
        return 0
    if a.cmd == "check":
        acc = A.resolve(p, a.n)
        print(f"№{acc.number}: сессия {asyncio.run(A.check_session(acc, p))}")
        return 0

    if a.cmd in ("reply", "inbox"):
        from lead_catcher import outreach as O
        if p.outreach is None:
            raise P.ProfileError("отправка выключена: в профиле нет outreach.enabled: true (решение человека)")
        acc = A.resolve(p, p.outreach.account)
        print(f"аккаунт №{acc.number} (outreach); сеть: {acc.net_label}")
        if a.cmd == "reply":
            return asyncio.run(O.reply(p, acc, Path(a.queue), send=a.send))
        return asyncio.run(O.inbox(p, acc))

    from lead_catcher import collect as C
    acc = A.resolve(p, None)
    print(f"аккаунт №{acc.number}; сеть: {acc.net_label}")
    if a.cmd == "find-chats":
        return asyncio.run(C.find_chats(p, acc, a.words))
    if a.cmd == "chat-info":
        return asyncio.run(C.chat_info(p, acc, a.handles))
    if a.cmd == "read-sample":
        return asyncio.run(C.read_sample(p, acc, a.groups, a.hours, a.max))
    if a.cmd == "search-groups":
        return asyncio.run(C.search_groups(p, acc, a.groups, a.words, a.days, a.limit))
    return 2
