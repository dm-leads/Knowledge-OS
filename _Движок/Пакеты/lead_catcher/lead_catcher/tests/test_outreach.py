"""Тесты касания (outreach): выключено по умолчанию, сухой прогон ничего не шлёт, фраза «кто пишет», один раз на
человека, лимит в сутки, окно, свежий стоп, стоп-сигнал при отправке, закрытая личка, стоп-лист по ответу.
Сеть не нужна: клиент Telegram подменяется."""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import json

import pytest

from lead_catcher import accounts as A
from lead_catcher import collect, outreach as O, profile as P, stops
from lead_catcher.tests.test_lead_catcher import make_instance

PHRASE = "Я Пётр, из магазина"
NOON = dt.datetime(2026, 12, 1, 9, 0, tzinfo=dt.timezone.utc)   # 12:00 при UTC+3


def _enable(inst, **over):
    cfg = {"per_day": 10, "interval_s": "[60, 60]", "window": "[10, 20]"} | over
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8") + (
        f"\nprivate_dir: \"{(inst.parent / 'private').as_posix()}\"\n"
        "outreach:\n  enabled: true\n  account: 1\n"
        f"  required_phrase: \"{PHRASE}\"\n  per_day: {cfg['per_day']}\n"
        f"  interval_s: {cfg['interval_s']}\n  window: {cfg['window']}\n"), encoding="utf-8")
    return P.load(inst)


def _queue(tmp, rows):
    f = tmp / "очередь.csv"
    with f.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["ссылка", "текст", "статус"])
        w.writerows(rows)
    return f


class _User:
    def __init__(self, uid, bot=False):
        self.id, self.bot, self.is_self, self.deleted, self.min, self.access_hash = uid, bot, False, False, False, 1


class _M:
    def __init__(self, mid, sender, days=1, text="вопрос", out=False):
        self.id, self._s, self.message, self.out = mid, sender, text, out
        self.date = NOON - dt.timedelta(days=days)

    async def get_sender(self):
        return self._s


def _client(msgs, sent, send_exc=None, replies=None):
    from telethon.tl.types import User

    class FC:
        async def connect(self): pass
        async def disconnect(self): pass
        async def is_user_authorized(self): return True
        async def get_input_entity(self, x): return x

        async def get_messages(self, peer, ids=None, min_id=None, limit=None):
            if min_id is not None:
                return (replies or {}).get(peer.id if hasattr(peer, "id") else peer, [])
            if isinstance(peer, str):
                return msgs.get((peer, ids))
            return _M(ids, None, text=sent[-1][1])

        async def send_message(self, peer, text):
            if send_exc:
                raise send_exc
            sent.append((peer.id, text))
            return _M(1000 + len(sent), None)
    return FC()


@pytest.fixture
def env(tmp_path, monkeypatch):
    real_sleep = asyncio.sleep

    async def no_sleep(s):
        await real_sleep(0)
    monkeypatch.setattr(O.asyncio, "sleep", no_sleep)
    inst = make_instance(tmp_path)
    return tmp_path, inst


def _patch_user(monkeypatch):
    """Подменный пользователь проходит проверку isinstance(sender, User)."""
    from telethon.tl import types
    monkeypatch.setattr(types, "User", _User)


def _run(p, q, send, now=NOON):
    acc = A.resolve(p, 1)
    return asyncio.run(O.reply(p, acc, q, send=send, now_fn=lambda: now))


def test_disabled_by_default(env):
    tmp, inst = env
    p = P.load(inst)
    assert p.outreach is None
    with pytest.raises(P.ProfileError, match="выключена"):
        _run(p, _queue(tmp, []), send=True)


def test_phrase_required_in_profile(env):
    tmp, inst = env
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8") + "\noutreach:\n  enabled: true\n  account: 1\n", encoding="utf-8")
    with pytest.raises(P.ProfileError, match="required_phrase"):
        P.load(inst)


def test_dry_run_sends_nothing(env, monkeypatch):
    tmp, inst = env
    _patch_user(monkeypatch)
    p = _enable(inst)
    sent = []
    monkeypatch.setattr(collect, "client", lambda acc: _client({("chat_a", 5): _M(5, _User(11))}, sent))
    q = _queue(tmp, [["https://t.me/chat_a/5", f"Здравствуйте! {PHRASE}. Вопрос актуален?", "отправить"]])
    assert _run(p, q, send=False) == 0
    assert sent == [] and O.journal(O.outreach_dir(p)) == []


def test_send_rules_and_once_per_person(env, monkeypatch):
    tmp, inst = env
    _patch_user(monkeypatch)
    p = _enable(inst)
    d = O.outreach_dir(p)
    O.add_blocked(d, 44, "тест")
    sent = []
    msgs = {("chat_a", 1): _M(1, _User(11)), ("chat_a", 2): _M(2, _User(22), days=30),
            ("chat_a", 3): _M(3, _User(33, bot=True)), ("chat_a", 4): _M(4, _User(44)),
            ("chat_a", 5): _M(5, _User(11)), ("chat_a", 6): _M(6, _User(66))}
    monkeypatch.setattr(collect, "client", lambda acc: _client(msgs, sent))
    ok = f"Здравствуйте! {PHRASE}. Вопрос актуален?"
    q = _queue(tmp, [[f"t.me/chat_a/{i}", ok, "отправить"] for i in (1, 2, 3, 4, 5)]
               + [["t.me/chat_a/6", "Здравствуйте! Вопрос актуален?", "отправить"],   # без фразы
                  ["t.me/chat_a/7", ok, "черновик"]])                                 # не одобрено
    assert _run(p, q, send=True) == 0
    assert sent == [(11, ok)]
    outs = [r["исход"] for r in O.journal(d)]
    assert outs[0] == "отправлено"
    assert any("30 дн" in x for x in outs) and any("бот" in x for x in outs)
    assert any("не писать" in x for x in outs) and any("уже писали" in x for x in outs)
    assert any("фразы" in x for x in outs) and len(outs) == 6
    assert _run(p, q, send=True) == 0          # повтор: никому второй раз
    assert sent == [(11, ok)] and len(O.journal(d)) == 6


def test_daily_limit(env, monkeypatch):
    tmp, inst = env
    _patch_user(monkeypatch)
    p = _enable(inst, per_day=2)
    sent = []
    msgs = {("chat_a", i): _M(i, _User(100 + i)) for i in range(1, 6)}
    monkeypatch.setattr(collect, "client", lambda acc: _client(msgs, sent))
    q = _queue(tmp, [[f"t.me/chat_a/{i}", f"{PHRASE}, текст {i}", "отправить"] for i in range(1, 6)])
    _run(p, q, send=True)
    assert len(sent) == 2
    _run(p, q, send=True)
    assert len(sent) == 2                       # в тот же день — лимит выбран
    _run(p, q, send=True, now=NOON + dt.timedelta(days=1))
    assert len(sent) == 4


def test_window_and_fresh_stop_refuse(env, monkeypatch):
    tmp, inst = env
    _patch_user(monkeypatch)
    p = _enable(inst)
    sent = []
    monkeypatch.setattr(collect, "client", lambda acc: _client({("chat_a", 1): _M(1, _User(11))}, sent))
    q = _queue(tmp, [["t.me/chat_a/1", f"{PHRASE}, текст", "отправить"]])
    night = dt.datetime(2026, 12, 1, 20, 0, tzinfo=dt.timezone.utc)   # 23:00 при UTC+3
    assert _run(p, q, send=True, now=night) == 2 and sent == []
    stops.record(p, 1, "PeerFlood — Telegram ограничил аккаунт")
    assert _run(p, q, send=True) == 2 and sent == []


def test_peer_flood_on_send_stops_and_records(env, monkeypatch):
    from telethon import errors
    tmp, inst = env
    _patch_user(monkeypatch)
    p = _enable(inst)
    sent = []
    exc = errors.PeerFloodError(request=None)
    monkeypatch.setattr(collect, "client", lambda acc: _client({("chat_a", 1): _M(1, _User(11))}, sent, exc))
    q = _queue(tmp, [["t.me/chat_a/1", f"{PHRASE}, текст", "отправить"]])
    with pytest.raises(stops.StopSignal):
        _run(p, q, send=True)
    rec = [json.loads(x) for x in (p.data_dir / "stops.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rec[-1]["account"] == 1 and "PeerFlood" in rec[-1]["kind"]


def test_privacy_restricted_is_skipped_not_stopped(env, monkeypatch):
    from telethon import errors
    tmp, inst = env
    _patch_user(monkeypatch)
    p = _enable(inst)
    sent = []
    exc = errors.UserPrivacyRestrictedError(request=None)
    monkeypatch.setattr(collect, "client", lambda acc: _client({("chat_a", 1): _M(1, _User(11))}, sent, exc))
    q = _queue(tmp, [["t.me/chat_a/1", f"{PHRASE}, текст", "отправить"]])
    assert _run(p, q, send=True) == 0
    assert O.journal(O.outreach_dir(p))[-1]["исход"].startswith("не доставлено")
    assert not (p.data_dir / "stops.jsonl").exists()


def test_inbox_opt_out_goes_to_stop_list(env, monkeypatch):
    tmp, inst = env
    _patch_user(monkeypatch)
    p = _enable(inst)
    d = O.outreach_dir(p)
    O._append(d / "журнал-отправок.jsonl", {"аккаунт": 1, "ссылка": "t.me/chat_a/1", "исход": "отправлено",
                                             "user_id": 11, "чат": "chat_a", "сообщение": 1, "наше": 1001})
    replies = {11: [_M(1002, None, text="Не пишите мне больше, это спам")]}
    monkeypatch.setattr(collect, "client", lambda acc: _client({}, [], replies=replies))
    assert asyncio.run(O.inbox(p, A.resolve(p, 1))) == 0
    assert 11 in O.blocked(d)
    assert len(O._jsonl(d / "ответы.jsonl")) == 1
