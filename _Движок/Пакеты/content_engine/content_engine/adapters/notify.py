"""Сводка пульса в мессенджер (Т34.5) — запись вовне со ступенью `notify.allow`.

Встроенные: `lark_webhook` — POST JSON на адрес вебхука бота группы (адрес — по имени переменной `notify.url_env`),
подтверждение — `{"code": 0}` в ответе; `telegram` — Bot API `sendMessage` (токен и id чата — по именам переменных),
подтверждение — `"ok": true`. Адаптер проекта — `module:<путь от инстанса>:<класс>` с методом `action(text) → Action`.
Белый список хоста — хост адреса из переменной: другой адрес запрос не получит.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from ..core.actions import Action
from ..core.env_file import secret
from ..core.errors import EngineError
from .write_client import WriteClient


def _lark(instance, text: str, transport) -> Action:
    url = secret(instance.p, instance.p["notify"]["url_env"])
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise EngineError("notify: адрес вебхука Lark — https-адрес (значение переменной notify.url_env)")
    base, endpoint = f"https://{parts.hostname}", parts.path.lstrip("/")
    client = WriteClient("Lark", base, {("POST", endpoint)}, {"Content-Type": "application/json"},
                         hosts=[parts.hostname], transport=transport)

    def do():
        try:
            return client.call("POST", endpoint, json={"msg_type": "text", "content": {"text": text}})
        except EngineError as exc:              # путь вебхука — сам секрет: в сообщение он не попадает
            raise EngineError(str(exc).replace(endpoint, "<вебхук>").replace(url, "<вебхук>")) from None

    def verify(answer):
        body = answer.data if isinstance(answer.data, dict) else {}
        code = body.get("code", body.get("StatusCode"))
        return answer.status == 200 and code == 0, f"HTTP {answer.status}, code {code}"

    return Action("notify_lark", f"{parts.hostname}/…", "POST", "сводка пульса", "status_code", ("notify", "write"),
                  do=do, verify=verify, needs_snapshot=False, meta={"client": client})


def _telegram(instance, text: str, transport) -> Action:
    n = instance.p["notify"]
    token, chat = secret(instance.p, n["url_env"]), secret(instance.p, n["chat_env"])
    endpoint = f"bot{token}/sendMessage"
    client = WriteClient("Telegram", "https://api.telegram.org", {("POST", endpoint)},
                         {"Content-Type": "application/json"}, hosts=["api.telegram.org"], transport=transport)

    def do():
        try:
            return client.call("POST", endpoint, json={"chat_id": chat, "text": text})
        except EngineError as exc:              # в адресе Bot API — токен: в сообщение он не попадает
            raise EngineError(str(exc).replace(token, "<токен>")) from None

    def verify(answer):
        body = answer.data if isinstance(answer.data, dict) else {}
        return answer.status == 200 and body.get("ok") is True, f"HTTP {answer.status}, ok {body.get('ok')}"

    return Action("notify_telegram", "api.telegram.org/bot…/sendMessage", "POST", "сводка пульса", "status_code",
                  ("notify", "write"), do=do, verify=verify, needs_snapshot=False, meta={"client": client})


def action(instance, text: str, *, transport=None) -> Action | None:
    """Действие отправки сводки; `None` — сводка не подключена (`notify.kind: none`)."""
    transport = transport if transport is not None else instance.transport
    kind = instance.p["notify"]["kind"]
    if kind == "none":
        return None
    if kind == "lark_webhook":
        return _lark(instance, text, transport)
    if kind == "telegram":
        return _telegram(instance, text, transport)
    from ..core.plugins import load_class
    act = load_class(instance, kind, {}, "notify.kind")(instance).action(text)
    if tuple(act.right) != ("notify", "write"):    # адаптер проекта не обходит право notify.allow
        raise EngineError(f"адаптер сводки {kind}: ступень действия {act.right} — нужна (notify, write)")
    return act
