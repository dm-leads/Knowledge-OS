"""Тесты пакета по критериям приёмки постановки (21.11.2026): 1 — нейтральность, 2 — маска секретов,
3 — новый номер без правки кода, 4 — стоп не переключает номер, 6 — onboard; плюс разрешённые чаты и обезличивание.
Сеть не нужна: пробы Telegram подменяются."""
from __future__ import annotations

import asyncio
import io
import re
import sys
from pathlib import Path

import pytest

import lead_catcher
from lead_catcher import accounts as A
from lead_catcher import cli, collect, profile as P, stops
from lead_catcher.onboard import onboard
from lead_catcher.redact import clean

PKG = Path(lead_catcher.__file__).parent
FAKE = {"ID1": "11111111", "HASH1": "a" * 32, "ID2": "22222222", "HASH2": "b" * 32,
        "ID3": "33333333", "HASH3": "c" * 32,
        # выдуманные значения собираются из частей, чтобы сканер секретов канона не принял тест за утечку
        "PROXY": "http://" + "proxyuser" + ":" + "proxypass99" + "@10.0.0.1:8000"}
MT_SECRET = "dd" + "0123456789abcdef"


def make_instance(tmp: Path, accounts=((1, "ID1", "HASH1"), (2, "ID2", "HASH2"))) -> Path:
    env = tmp / "secrets" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text("\n".join(f"{k}={v}" for k, v in FAKE.items()), encoding="utf-8")
    inst = tmp / "proj" / "lead_catcher_instance"
    onboard(inst, str(env), str(tmp / "sessions"), list(accounts), ["PROXY"])
    return inst


# 1. Нейтральность: в коде пакета нет имён проектов, путей проектов и имён переменных конкретных ключей.
def test_package_is_neutral():
    banned = re.compile(r"Бризекс|Breezeks|breezeks|Учебный центр|ЖК|TG_API_MAIN|Second Brain|redmi", re.I)
    hits = [f"{f.name}:{i}" for f in PKG.rglob("*.py") if "tests" not in f.parts
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if banned.search(line)]
    assert hits == []


# 6. onboard создаёт профиль, список чатов, data/, .gitignore; не перезаписывает; сессии в git — отказ.
def test_onboard_creates_and_refuses(tmp_path):
    inst = make_instance(tmp_path)
    for name in ("profile.yaml", "allowed_chats.txt", ".gitignore"):
        assert (inst / name).is_file()
    assert "*.session" in (inst / ".gitignore").read_text(encoding="utf-8")
    p = P.load(inst)
    assert sorted(p.accounts) == [1, 2] and p.active == 1
    with pytest.raises(SystemExit):
        onboard(inst, "x", str(tmp_path / "sessions"), [(1, "ID1", "HASH1")], [])
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(SystemExit, match="git"):
        onboard(repo / "inst", "x", str(repo / "sessions"), [(1, "ID1", "HASH1")], [])


def test_profile_rejects_values_instead_of_names(tmp_path):
    inst = make_instance(tmp_path)
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("api_id: ID1", "api_id: 11111111"), encoding="utf-8")
    with pytest.raises(P.ProfileError):
        P.load(inst)


# 2. Маска: значения ключей и логин/пароль прокси не встречаются в выводе status.
def test_status_output_has_no_secrets(tmp_path, monkeypatch):
    inst = make_instance(tmp_path)

    async def fake_probe(acc):
        return f"приняты (id {acc.api_id}, hash {acc.api_hash})"  # нарочно пытается напечатать секреты

    async def fake_check(acc, p=None):
        return "входа нет"

    monkeypatch.setattr(A, "probe_keys", fake_probe)
    monkeypatch.setattr(A, "check_session", fake_check)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert cli.main(["--instance", str(inst), "status"]) == 0
    out = buf.getvalue()
    assert "№1" in out and "№2" in out
    for v in ("11111111", "a" * 32, "22222222", "b" * 32, "proxyuser", "proxypass99"):
        assert v not in out, v
    assert "10.0.0.1:8000" in out  # адрес прокси без логина виден — по нему понятно, какой путь


# 3. Новый номер — только строками .env и профиля, без правки кода.
def test_third_account_by_config_only(tmp_path, monkeypatch):
    inst = make_instance(tmp_path)
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace(
        "data_dir:", "  3: {api_id: ID3, api_hash: HASH3, session: tg_reader_3, proxy: null}\ndata_dir:"),
        encoding="utf-8")
    p = P.load(inst)
    assert sorted(p.accounts) == [1, 2, 3]
    acc = A.resolve(p, 3)
    assert acc.api_id == 33333333 and acc.session_path.name == "tg_reader_3"
    assert "PROXY" in acc.net_label


def test_same_session_for_two_numbers_rejected(tmp_path):
    inst = make_instance(tmp_path)
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("session: tg_reader_2", "session: tg_reader_1"),
                 encoding="utf-8")
    with pytest.raises(P.ProfileError, match="одна сессия"):
        P.load(inst)


# 4. Стоп-сигнал останавливает сбор, пишет журнал и НЕ переключает активный номер.
def test_stop_does_not_switch(tmp_path, monkeypatch):
    from telethon import errors

    inst = make_instance(tmp_path)
    (inst / "allowed_chats.txt").write_text("@good_chat\n", encoding="utf-8")
    (tmp_path / "sessions" / "tg_reader_2.session").write_bytes(b"")
    p = P.load(inst)
    acc = A.resolve(p, 1)

    class FakeClient:
        async def connect(self): pass
        async def disconnect(self): pass
        async def is_user_authorized(self): return True

        def iter_messages(self, *a, **k):
            async def gen():
                raise errors.PeerFloodError(request=None)
                yield  # noqa
            return gen()

    monkeypatch.setattr(collect, "client", lambda acc: FakeClient())
    monkeypatch.setattr(collect, "pause", lambda *a, **k: asyncio.sleep(0))
    with pytest.raises(stops.StopSignal) as e:
        asyncio.run(collect.read_sample(p, acc, ["@good_chat"]))
    assert e.value.code == stops.STOP_EXIT and e.value.account == 1
    assert P.load(inst).active == 1
    assert A.last_stop(p, 1)["kind"].startswith("PeerFlood")


def test_flood_wait_classification():
    from telethon import errors

    assert stops.classify(errors.FloodWaitError(request=None, capture=301)).startswith("ожидание")
    assert stops.classify(errors.FloodWaitError(request=None, capture=30)) is None
    assert stops.classify(ValueError()) is None


def test_use_changes_only_active_line(tmp_path):
    inst = make_instance(tmp_path)
    before = (inst / "profile.yaml").read_text(encoding="utf-8")
    out, err = sys.stdout, sys.stderr
    try:
        cli.main(["--instance", str(inst), "use", "2"])
    finally:
        sys.stdout, sys.stderr = out, err
    after = (inst / "profile.yaml").read_text(encoding="utf-8")
    assert P.load(inst).active == 2
    assert [l for l in before.splitlines() if not l.startswith("active:")] == \
           [l for l in after.splitlines() if not l.startswith("active:")]


def test_reading_outside_allowed_chats_refused(tmp_path):
    inst = make_instance(tmp_path)
    (inst / "allowed_chats.txt").write_text("@good_chat\nhttps://t.me/other_chat\n", encoding="utf-8")
    p = P.load(inst)
    assert collect.guard_allowed(p, ["@Good_Chat", "t.me/other_chat"]) == ["good_chat", "other_chat"]
    with pytest.raises(P.ProfileError, match="нет в"):
        collect.guard_allowed(p, ["@good_chat", "@strange"])


def test_redact():
    s = clean("Позвоните +7 (812) 345-67-89, пишите @ivan_petrov или test@mail.ru, кв. 45, t.me/somechat")
    for bad in ("812", "ivan_petrov", "mail.ru", "45", "somechat"):
        assert bad not in s


# --- по находкам независимой проверки 21.11.2026 ---
def test_mask_covers_mtproto_secret_and_short_password(tmp_path):
    inst = make_instance(tmp_path)
    env = tmp_path / "secrets" / ".env"
    env.write_text(env.read_text(encoding="utf-8") +
                   "\nMT=tg://proxy?server=1.2.3.4&port=443&" + "secret" + "=" + MT_SECRET + "\n"
                   "SHORTP=http://u1:abc@1.1.1.1:80\n", encoding="utf-8")
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("proxy_env: [PROXY]", "proxy_env: [MT, SHORTP, PROXY]"),
                 encoding="utf-8")
    p = P.load(inst)
    assert MT_SECRET not in p.mask("secret" + "=" + MT_SECRET)
    assert "abc" not in p.mask("pass abc end")


def test_redact_social_links():
    s = clean("пишите vk.com/id12345 или https://instagram.com/ivan.petrov, tg://user?id=123456")
    for bad in ("id12345", "ivan.petrov", "123456"):
        assert bad not in s


def test_onboard_refuses_secrets_in_git(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(SystemExit, match="секретов"):
        onboard(repo / "inst", str(repo / ".env"), str(tmp_path / "sessions"), [(1, "ID1", "HASH1")], [])


def test_split_window_and_url():
    assert P.split_window("@chat:72") == ("@chat", 72)
    assert P.split_window("https://t.me/chat") == ("https://t.me/chat", None)
    assert P.split_window("https://t.me/chat:48") == ("https://t.me/chat", 48)


def test_bad_proxy_port_is_profile_error():
    with pytest.raises(P.ProfileError):
        A.parse_proxy("X", "http://h:99999")
    with pytest.raises(P.ProfileError):
        A.parse_proxy("X", "http://h:abc")
    assert A.parse_proxy("X", "socks5h://h:1080")[1]["proxy"]["proxy_type"] == "socks5"


def test_proxy_env_must_be_list(tmp_path):
    inst = make_instance(tmp_path)
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("proxy_env: [PROXY]", "proxy_env: PROXY"), encoding="utf-8")
    with pytest.raises(P.ProfileError, match="список"):
        P.load(inst)


class _Msg:
    def __init__(self, i, text):
        import datetime as dt
        self.id, self.message, self.date = i, text, dt.datetime.now(dt.timezone.utc)


def _fake_client(script):
    """script: {чат: список сообщений или исключение}."""
    class FC:
        async def connect(self): pass
        async def disconnect(self): pass
        async def is_user_authorized(self): return True

        def iter_messages(self, g, **k):
            item = script[g]

            async def gen():
                if isinstance(item, BaseException):
                    raise item
                for m in item:
                    yield m
            return gen()
    return FC()


def test_flood_wait_under_5min_is_waited_not_skipped(tmp_path, monkeypatch):
    from telethon import errors

    inst = make_instance(tmp_path)
    (inst / "allowed_chats.txt").write_text("@a\n@b\n", encoding="utf-8")
    p = P.load(inst)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(collect.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(collect, "pause", lambda *a, **k: fake_sleep(0))
    monkeypatch.setattr(collect, "client", lambda acc: _fake_client(
        {"a": errors.FloodWaitError(request=None, capture=200), "b": [_Msg(1, "душно в квартире")]}))
    rc = asyncio.run(collect.read_sample(p, A.resolve(p, 1), ["@a", "@b"]))
    assert rc == 0 and 205 in slept
    assert A.last_stop(p, 1) is None


def test_search_counts_in_day_cap(tmp_path, monkeypatch):
    inst = make_instance(tmp_path)
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("messages_per_day_to_model: 500",
                                                       "messages_per_day_to_model: 3"), encoding="utf-8")
    (inst / "allowed_chats.txt").write_text("@a\n", encoding="utf-8")
    p = P.load(inst)
    monkeypatch.setattr(collect, "pause", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(collect, "client", lambda acc: _fake_client({"a": [_Msg(i, f"текст {i}") for i in range(5)]}))
    asyncio.run(collect.search_groups(p, A.resolve(p, 1), ["@a"], ["душно"]))
    assert collect.day_left(p, collect.out_dir(p)) == 0
    assert asyncio.run(collect.read_sample(p, A.resolve(p, 1), ["@a"])) == 0  # потолок выбран — чтения нет


# --- 1.1.0: общее хранилище сессий устройства и замок ---
def test_session_lock_blocks_second_holder(tmp_path):
    from lead_catcher.lock import SessionBusy, SessionLock

    f = tmp_path / "tg_reader_2.session"
    a = SessionLock(f, "проект А").acquire()
    try:
        with pytest.raises(SessionBusy, match="проект А"):
            SessionLock(f, "проект Б").acquire()
    finally:
        a.release()
    SessionLock(f, "проект Б").acquire().release()  # после снятия — свободна


def test_check_session_reports_busy(tmp_path):
    from lead_catcher.lock import SessionLock

    inst = make_instance(tmp_path)
    p = P.load(inst)
    acc = A.resolve(p, 2)
    acc.session_file.write_bytes(b"")
    held = SessionLock(acc.session_file, "другой проект").acquire()
    try:
        assert asyncio.run(A.check_session(acc, p)).startswith("занята")
    finally:
        held.release()


def test_onboard_accepts_gitignored_store_inside_repo(tmp_path):
    import subprocess

    repo = tmp_path / "kb"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / ".gitignore").write_text("/store/\n", encoding="utf-8")
    store = repo / "store" / "telegram"
    store.mkdir(parents=True)
    (store / ".env").write_text("ID1=1\n", encoding="utf-8")
    made = onboard(tmp_path / "proj" / "inst", str(store / ".env"), str(store), [(1, "ID1", "HASH1")], [])
    assert made
    with pytest.raises(SystemExit, match="gitignore"):
        onboard(tmp_path / "proj" / "inst2", str(store / ".env"), str(repo / "open"), [(1, "ID1", "HASH1")], [])


# --- 1.2.0: карточки групп и администраторы ---
def _chatinfo_client():
    from types import SimpleNamespace as NS

    class FC:
        async def connect(self): pass
        async def disconnect(self): pass
        async def is_user_authorized(self): return True
        async def get_entity(self, name): return NS(title=f"ЖК {name}")

        async def __call__(self, req):
            return NS(full_chat=NS(about="Админ сети чатов: @net_admin_x, реклама — t.me/net_ads", participants_count=4000,
                                   linked_chat_id=None))

        def iter_participants(self, ent, filter=None):
            async def gen():
                yield NS(username="admin_one", bot=False)
                yield NS(username=None, bot=True)
            return gen()
    return FC()


def test_chat_info_needs_private_dir(tmp_path, monkeypatch):
    inst = make_instance(tmp_path)
    p = P.load(inst)
    monkeypatch.setattr(collect, "client", lambda acc: _chatinfo_client())
    with pytest.raises(P.ProfileError, match="private_dir"):
        asyncio.run(collect.chat_info(p, A.resolve(p, 1), ["@a_chat"]))


def test_chat_info_keeps_contacts_out_of_project_data(tmp_path, monkeypatch):
    inst = make_instance(tmp_path)
    f = inst / "profile.yaml"
    f.write_text(f.read_text(encoding="utf-8") + f'private_dir: "{(tmp_path / "private").as_posix()}"\n',
                 encoding="utf-8")
    p = P.load(inst)
    monkeypatch.setattr(collect, "client", lambda acc: _chatinfo_client())
    monkeypatch.setattr(collect, "pause", lambda *a, **k: asyncio.sleep(0))
    assert asyncio.run(collect.chat_info(p, A.resolve(p, 1), ["@a_chat", "@b_chat"])) == 0
    public = "".join(x.read_text(encoding="utf-8-sig") for x in p.data_dir.rglob("описания чатов-*.csv"))
    private = "".join(x.read_text(encoding="utf-8-sig") for x in (tmp_path / "private").rglob("*.csv"))
    assert "@a_chat" in public and ";да;" in public
    for secret in ("net_admin_x", "admin_one", "net_ads"):
        assert secret not in public and secret in private


# --- вход: номер и способ доставки кода (1.2.1) ---

def test_normalize_phone():
    body = "912" + "0000001"                      # фейковый номер собран из частей — сканер ПДн канона
    want = "+" + "7" + body
    assert A.normalize_phone("8" + " (" + body[:3] + ") " + body[3:]) == want
    assert A.normalize_phone("+" + "7 " + body[:3] + " " + body[3:]) == want
    assert A.normalize_phone("7" + body) == want
    with pytest.raises(P.ProfileError):
        A.normalize_phone("12345")


def test_describe_code_app_then_sms():
    from telethon.tl.types import auth as T
    sent = T.SentCode(type=T.SentCodeTypeApp(length=5), phone_code_hash="h",
                      next_type=T.CodeTypeSms(), timeout=60)
    text = A.describe_code(sent)
    assert "НЕ по SMS" in text and "по SMS" in text.split("Не пришёл")[1] and "60" in text
    last = T.SentCode(type=T.SentCodeTypeSms(length=5), phone_code_hash="h")
    assert "Другого способа" in A.describe_code(last)
