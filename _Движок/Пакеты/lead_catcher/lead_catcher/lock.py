"""Замок на сессию: один файл сессии одновременно работает только в одном процессе.

Сессии хранятся в одном месте на устройстве и общие для проектов; два проекта, открывшие одну сессию сразу,
ломают её (Telegram отзывает ключ как украденный). Замок — блокировка файла <сессия>.session.lock средствами ОС:
снимается сама, если процесс упал. Кто держит замок — в <сессия>.session.lock.owner (папка проекта, время).
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from lead_catcher.profile import ProfileError


class SessionBusy(ProfileError):
    pass


class SessionLock:
    def __init__(self, session_file: Path, owner: str):
        self.path = session_file.with_name(session_file.name + ".lock")
        self.owner_path = session_file.with_name(session_file.name + ".lock.owner")
        self.owner = owner
        self.fd: int | None = None

    def acquire(self) -> "SessionLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            who = ""
            try:
                who = self.owner_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
            raise SessionBusy(f"сессия {self.path.name[:-5]} сейчас занята другим процессом"
                              + (f" ({who})" if who else "") + " — дождитесь конца его работы")
        self.fd = fd
        self.owner_path.write_text(f"{self.owner}; с {dt.datetime.now():%d.%m.%Y %H:%M:%S}; pid {os.getpid()}",
                                   encoding="utf-8")
        return self

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None
            try:
                self.owner_path.unlink()
            except OSError:
                pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
