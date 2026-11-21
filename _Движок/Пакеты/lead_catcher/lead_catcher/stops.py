"""Стоп-правила аккаунта (методология, раздел «Стоп-правила»): PeerFlood или @SpamBot, ожидание дольше 5 минут,
бан, отозванная сессия — немедленный стоп. Возобновление и переход на другой номер — только решением человека:
код номер не переключает (автоматическая смена аккаунта после ограничения — обход ограничений Telegram).
"""
from __future__ import annotations

import datetime as dt
import json

from lead_catcher.profile import Profile

MAX_WAIT_S = 300
STOP_EXIT = 4


class StopSignal(SystemExit):
    def __init__(self, account: int, kind: str, detail: str = ""):
        self.account, self.kind, self.detail = account, kind, detail
        super().__init__(STOP_EXIT)


def classify(e: BaseException) -> str | None:
    """Вид стоп-сигнала или None (ошибка не про аккаунт: закрытая группа, сеть)."""
    from telethon import errors as E

    if isinstance(e, E.FloodWaitError):
        return f"ожидание {e.seconds} с" if e.seconds > MAX_WAIT_S else None
    table = [
        ("PeerFloodError", "PeerFlood — Telegram ограничил аккаунт"),
        ("UserDeactivatedBanError", "аккаунт заблокирован"),
        ("UserDeactivatedError", "аккаунт удалён или отключён"),
        ("PhoneNumberBannedError", "номер заблокирован"),
        ("AuthKeyUnregisteredError", "сессия отозвана"),
        ("SessionRevokedError", "сессия отозвана"),
        ("SessionExpiredError", "сессия истекла"),
        ("AuthKeyDuplicatedError", "сессия использована в двух местах — отозвана"),
        ("UserRestrictedError", "аккаунт ограничен (жалобы)"),
    ]
    for name, kind in table:
        cls = getattr(E, name, None)
        if cls is not None and isinstance(e, cls):
            return kind
    return None


def record(p: Profile, account: int, kind: str, detail: str = "") -> dict:
    p.data_dir.mkdir(parents=True, exist_ok=True)
    rec = {"time": dt.datetime.now().isoformat(timespec="seconds"), "account": account, "kind": kind,
           "detail": p.mask(detail)[:300]}
    with (p.data_dir / "stops.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def stop(p: Profile, account: int, kind: str, detail: str = "") -> StopSignal:
    """Записать стоп и вернуть исключение; вызывающий его поднимает. Номер не переключается."""
    record(p, account, kind, detail)
    ready = [n for n, a in sorted(p.accounts.items())
             if n != account and (p.sessions_dir / f"{a.session}.session").is_file()]
    print(f"СТОП, аккаунт №{account}: {kind}. Сбор остановлен, номер НЕ переключён.")
    print("  Номера со входом: " + (", ".join(map(str, ready)) if ready else "нет") +
          ". Переключить — решением человека: `use N`.")
    return StopSignal(account, kind, detail)
