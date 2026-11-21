"""Команды ловца лидов. Запуск: uv run --project <пакет> python -m lead_catcher --instance <папка экземпляра> <команда>

  onboard --secrets-env P --sessions-dir P --account N:ИМЯ_ID:ИМЯ_HASH [--proxy-env ИМЯ …]   подключить проект
  status                     по каждому номеру: ключи (проба без входа), вход, сессия, последний стоп
  login N                    вход по номеру (телефон, код, пароль вводит человек в терминале)
  check [N]                  жива ли сессия номера (по умолчанию — активного)
  use N                      сделать номер активным (решение человека)
  find-chats СЛОВО|@адрес …  карта открытых групп: название, участники, живость (без текстов)
  read-sample @чат[:часы] …  [--hours 24 --max 300]   пробное чтение разрешённых чатов
  search-groups --groups @чат … --words слово …  [--days 365 --limit 30]   поиск по истории разрешённых чатов

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
    ap = argparse.ArgumentParser(prog="lead_catcher", description=f"Ловец лидов {__version__} — только чтение")
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
    ck = sub.add_parser("check")
    ck.add_argument("n", type=int, nargs="?")
    u = sub.add_parser("use")
    u.add_argument("n", type=int)
    fc = sub.add_parser("find-chats")
    fc.add_argument("words", nargs="+")
    rs = sub.add_parser("read-sample")
    rs.add_argument("groups", nargs="+")
    rs.add_argument("--hours", type=int, default=24)
    rs.add_argument("--max", type=int, default=300)
    sg = sub.add_parser("search-groups")
    sg.add_argument("--groups", nargs="+", required=True)
    sg.add_argument("--words", nargs="+", required=True)
    sg.add_argument("--days", type=int, default=365)
    sg.add_argument("--limit", type=int, default=30)
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
        acc = A.resolve(p, a.n)
        print("сеть:", acc.net_label)
        print(asyncio.run(A.login(acc)))
        return 0
    if a.cmd == "check":
        acc = A.resolve(p, a.n)
        print(f"№{acc.number}: сессия {asyncio.run(A.check_session(acc, p))}")
        return 0

    from lead_catcher import collect as C
    acc = A.resolve(p, None)
    print(f"аккаунт №{acc.number}; сеть: {acc.net_label}")
    if a.cmd == "find-chats":
        return asyncio.run(C.find_chats(p, acc, a.words))
    if a.cmd == "read-sample":
        return asyncio.run(C.read_sample(p, acc, a.groups, a.hours, a.max))
    if a.cmd == "search-groups":
        return asyncio.run(C.search_groups(p, acc, a.groups, a.words, a.days, a.limit))
    return 2
