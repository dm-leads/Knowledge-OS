# -*- coding: utf-8 -*-
"""Тесты kos_sync.py: скан добавленных строк и полный путь «коммит → хук → GitHub» на временных репозиториях.

    py -3 -m pytest -q test_kos_sync.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kos_sync as K  # noqa: E402

FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"          # собраны из частей, чтобы сам тест не ловился сканами
FAKE_PHONE = "+7 9" + "21 123-45-67"
FAKE_MAIL = "ivan.petrov" + "@" + "mail.ru"


def test_scan_finds_secret_and_pii():
    found = K.scan_lines([f"OPENAI_API_KEY={FAKE_KEY}", "звонить " + FAKE_PHONE, "почта " + FAKE_MAIL])
    classes = sorted(c for c, _ in found)
    assert "секрет" in classes and "телефон" in classes and "почта" in classes
    assert all(FAKE_KEY not in f for _, f in found)          # в отчёт значение не попадает


def test_scan_quiet_on_normal_text():
    lines = ["Co-Authored-By: Claude <noreply@anthropic.com>", "ключ — в переменной `KEYSO_API_KEY` (.env)",
             "счётчик 105977811, проект 11797013", "тел. формат 8 800 — горячая линия", "user@example.com"]
    assert K.scan_lines(lines) == []


def test_proxy_password_in_url_is_secret():
    assert K.scan_lines(["PROXY=http://user:" + "s3cretPass@1.2.3.4:8080"])


def run(cwd, *args, env=None):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, **(env or {})})


@pytest.fixture
def canon(tmp_path):
    """Голый «GitHub» + клон с копией kos_sync.py и хуком по тем же путям, что в каноне."""
    if not shutil.which("git"):
        pytest.skip("нет git")
    origin = tmp_path / "origin.git"
    run(tmp_path, "git", "init", "--bare", "-q", "-b", "main", str(origin))
    work = tmp_path / "canon"
    run(tmp_path, "git", "clone", "-q", str(origin), str(work))
    for a, b in (("user.name", "t"), ("user.email", "t@t"), ("core.autocrlf", "false")):
        run(work, "git", "config", a, b)
    (work / "_Движок" / "Инструменты").mkdir(parents=True)
    shutil.copy(HERE / "kos_sync.py", work / "_Движок" / "Инструменты" / "kos_sync.py")
    shutil.copytree(HERE.parent / "Хуки" / "git", work / "_Движок" / "Хуки" / "git")
    (work / "a.md").write_text("старт\n", encoding="utf-8")
    run(work, "git", "add", "-A")
    run(work, "git", "commit", "-q", "-m", "init")
    run(work, "git", "push", "-q", "-u", "origin", "main")
    assert run(work, sys.executable, "_Движок/Инструменты/kos_sync.py", "install").returncode == 0
    return work, origin


def remote_head(origin):
    return run(origin, "git", "rev-parse", "main").stdout.strip()


def test_commit_is_pushed_by_hook(canon):
    work, origin = canon
    (work / "b.md").write_text("методология\n", encoding="utf-8")
    run(work, "git", "add", "b.md")
    r = run(work, "git", "commit", "-q", "-m", "добавлена методология")
    assert "отправлено на GitHub" in r.stdout + r.stderr
    assert remote_head(origin) == run(work, "git", "rev-parse", "HEAD").stdout.strip()


def test_secret_blocks_push(canon):
    work, origin = canon
    before = remote_head(origin)
    (work / "c.md").write_text(f"TOKEN={FAKE_KEY}\n", encoding="utf-8")
    run(work, "git", "add", "c.md")
    r = run(work, "git", "commit", "-q", "-m", "случайно ключ")
    assert "ОТПРАВКА ОСТАНОВЛЕНА" in r.stdout + r.stderr
    assert remote_head(origin) == before
    assert run(work, sys.executable, "_Движок/Инструменты/kos_sync.py", "push").returncode == 1


def test_pull_brings_other_device_commits(canon, tmp_path):
    work, origin = canon
    other = tmp_path / "other"
    run(tmp_path, "git", "clone", "-q", str(origin), str(other))
    for a, b in (("user.name", "o"), ("user.email", "o@o")):
        run(other, "git", "config", a, b)
    (other / "d.md").write_text("с другого устройства\n", encoding="utf-8")
    run(other, "git", "add", "d.md")
    run(other, "git", "commit", "-q", "-m", "другое устройство")
    run(other, "git", "push", "-q")
    r = run(work, sys.executable, "_Движок/Инструменты/kos_sync.py", "pull", "--brief")
    assert "подтянуто 1" in r.stdout
    assert (work / "d.md").exists()


def test_diverged_with_clean_tree_rebases_and_pushes(canon, tmp_path):
    work, origin = canon
    other = tmp_path / "other2"
    run(tmp_path, "git", "clone", "-q", str(origin), str(other))
    for a, b in (("user.name", "o"), ("user.email", "o@o")):
        run(other, "git", "config", a, b)
    (other / "e.md").write_text("чужое\n", encoding="utf-8")
    run(other, "git", "add", "e.md")
    run(other, "git", "commit", "-q", "-m", "чужое")
    run(other, "git", "push", "-q")
    (work / "f.md").write_text("своё\n", encoding="utf-8")
    run(work, "git", "add", "f.md")
    run(work, "git", "commit", "-q", "-m", "своё")             # хук: позади на 1 → перебазирование → отправка
    log = run(origin, "git", "log", "--format=%s", "main").stdout
    assert "своё" in log and "чужое" in log
