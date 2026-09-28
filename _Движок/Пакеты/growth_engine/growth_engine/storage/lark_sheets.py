"""Мост Lark Sheets для хранилища (задача 5.5): процесс `lark_sheets_cli.mjs serve` с командами tabs · read · write ·
addtab · insert. Логика таблицы поверх моста — общая с Google, в `storage/table.py`; `append_rows` не используется
(молча не пишет).

Отказ токена — ошибка доступа (покрытие), а не «пусто»; текст ошибок моста — без строки запроса ссылок, адресов почты
и длинных токенов. Скрипт моста лежит рядом с этим модулем (с 28.09.2026 — внутри пакета, чтобы уезжать вместе с
ним); другой скрипт задаётся переменной `LARK_SHEETS_BRIDGE`.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path

from ..core.errors import GuardViolation
from .table import MAX_CELLS, MAX_RANGES, TableBackend, column_letter, redact

__all__ = ["LarkBridge", "LarkBackend", "bridge_error", "bridge_script", "column_letter", "redact", "MAX_CELLS",
           "MAX_RANGES"]

BRIDGE_SCRIPT = Path(__file__).resolve().parent / "lark_sheets_cli.mjs"
# Отказ токена в ответе сервера Lark MCP: «UserAccessToken is invalid or expired» и коды Lark 99991663/99991668/99991677.
ACCESS_MARKERS = ("useraccesstoken", "user_access_token", "access token", "99991663", "99991668", "99991677")


def bridge_script() -> Path:
    """Скрипт моста: из `LARK_SHEETS_BRIDGE`, иначе рядом с этим модулем."""
    given = os.environ.get("LARK_SHEETS_BRIDGE")
    return Path(given) if given else BRIDGE_SCRIPT


def bridge_error(message: str) -> GuardViolation:
    """Ошибка моста: отказ токена — нехватка доступа (покрытие), остальное — нарушение записи или чтения."""
    if any(marker in message.lower() for marker in ACCESS_MARKERS):
        return GuardViolation(13, "нет доступа к Lark: токен пользователя недействителен или истёк — нужна повторная "
                                  "авторизация; данные не подставляются", GuardViolation.COVERAGE)
    return GuardViolation(13, f"мост Lark: {redact(message)}")


class LarkBridge:
    """Постоянный процесс моста: команда — строка JSON в stdin, ответ — строка JSON из stdout.

    Ответ ждётся не дольше `timeout` секунд, затем процесс останавливается. Вывод ошибок процесса копится во временном
    файле и при падении моста попадает в сообщение; длинные токены и адреса почты в нём скрываются.
    """

    def __init__(self, script: Path | None = None, node: str = "node", timeout: float = 180):
        self.timeout, self.next_id = timeout, 0
        self._log = tempfile.TemporaryFile()
        self.process = subprocess.Popen([node, str(script or bridge_script()), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self._log, text=True, encoding="utf-8", bufsize=1)
        self._replies = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def __enter__(self) -> "LarkBridge":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _pump(self) -> None:
        for line in self.process.stdout:
            self._replies.put(line)
        self._replies.put("")

    def _stopped(self, reason: str) -> GuardViolation:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        self._log.seek(0)
        text = " ".join(self._log.read().decode("utf-8", "replace").split())
        tail = redact(text)[-400:]
        return GuardViolation(13, f"мост Lark {reason}; вывод процесса: {tail or 'пуст'}", GuardViolation.COVERAGE)

    def call(self, command: str, **args) -> dict:
        self.next_id += 1
        request = json.dumps({"id": self.next_id, "command": command, "args": args}, ensure_ascii=False)
        try:
            self.process.stdin.write(request + "\n")
            self.process.stdin.flush()
        except OSError:
            raise self._stopped("не принимает команды") from None
        try:
            line = self._replies.get(timeout=self.timeout)
        except queue.Empty:
            raise self._stopped(f"не ответил за {self.timeout:g} с — процесс остановлен") from None
        if not line:
            raise self._stopped("завершился без ответа")
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            raise GuardViolation(13, "мост Lark ответил не строкой JSON — чтение ответов сбилось") from None
        if reply.get("id") != self.next_id:
            raise GuardViolation(13, "мост Lark ответил не на ту команду — чтение ответов сбилось")
        if not reply.get("ok"):
            raise bridge_error(str(reply.get("error", "")))
        return reply.get("result") or {}

    def close(self) -> None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self._log.close()


# Прежнее имя общей логики таблицы: тесты и инстанс звали её бэкендом Lark.
LarkBackend = TableBackend
