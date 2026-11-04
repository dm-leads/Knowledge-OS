# -*- coding: utf-8 -*-
"""kos_sync.py — одна картина канона локально и на GitHub: подтянуть при старте, отправить после коммита.

Зачем (04.11.2026): правило «отправка — сама после блока изменений» жило только в тексте. Чаты подключённых проектов
грузят общее правило «push — по команде владельца» и не отправляли: к 04.11 на машине лежали 5 неотправленных
коммитов. Теперь синхронизация — в коде:
  * git-хук post-commit канона (`_Движок/Хуки/git/post-commit`, подключается `core.hooksPath`) после каждого коммита
    вызывает `push`: скан добавленных строк на секреты и ПДн → при чистом скане `git push --follow-tags`;
  * хук начала сессии Claude Code вызывает `pull --brief`: подтянуть чужие изменения (только перемотка вперёд) и
    одной строкой сказать агенту, в каком состоянии канон.

Отправляется только то, что уже закоммичено; незакоммиченное не трогается. Ветка с расхождением подтягивается
перебазированием, только когда в отслеживаемых файлах нет чужих правок; иначе — сообщение и стоп, без автоматики.

    py -3 kos_sync.py status            # отставание и опережение, незакоммиченные пути
    py -3 kos_sync.py pull [--brief]    # подтянуть (ff-only); --brief — одна строка для хука начала сессии
    py -3 kos_sync.py push [--hook]     # скан + отправка; --hook — вызов из post-commit (код возврата всегда 0)
    py -3 kos_sync.py install           # core.hooksPath канона → _Движок/Хуки/git
Код возврата: 0 — синхронно или отправлено; 1 — скан нашёл секрет/ПДн; 2 — сеть или git отказали; 3 — нужна ручная
работа (расхождение при чужих правках).
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # D:\Knowledge-OS
HOOKS = "_Движок/Хуки/git"

SECRET = [
    re.compile(r"(?i)\b(sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(ghp|gho|github_pat|glpat|xox[bap])[-_][A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)\b[A-Z0-9_]*(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*\s*[=:]\s*['\"]?[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.]{24,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b[a-z]+://[^/\s:@]+:[^/\s:@]{6,}@"),          # логин:пароль в адресе (прокси, база)
]
PII = [
    ("почта", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("телефон", re.compile(r"(?<![\d\-])(\+7|8)[\s\-\(]*9\d{2}[\s\-\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")),
]
PII_ALLOW = re.compile(r"(?i)noreply@|example\.(com|ru|org)|@users\.noreply|@anthropic\.com|user@|name@|test@")


def git(*args, check=False):
    # KOS_SYNC=1 — git внутри синхронизации (перебазирование вызывает post-commit): хук видит её и молчит
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env={**os.environ, "KOS_SYNC": "1"})
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r


def counts():
    """(впереди, позади) относительно upstream после fetch; None — upstream нет."""
    if git("rev-parse", "--abbrev-ref", "@{u}").returncode:
        return None
    f = git("fetch", "--quiet", "origin")
    if f.returncode:
        raise ConnectionError(f.stderr.strip()[:200] or "fetch не прошёл")
    ahead, behind = git("rev-list", "--left-right", "--count", "HEAD...@{u}", check=True).stdout.split()
    return int(ahead), int(behind)


def tracked_dirty():
    return bool(git("status", "--porcelain", "--untracked-files=no").stdout.strip())


def scan_lines(lines):
    """Находки в добавленных строках: [(класс, фрагмент в маске)]."""
    found = []
    for line in lines:
        for rx in SECRET:
            m = rx.search(line)
            if m:
                found.append(("секрет", m.group(0)[:6] + "…"))
        for cls, rx in PII:
            for m in rx.finditer(line):
                if not PII_ALLOW.search(m.group(0)):
                    found.append((cls, m.group(0)[:3] + "…"))
    return found


def added_lines(rng):
    out = git("diff", "--no-color", "--unified=0", rng, check=True).stdout
    return [l[1:] for l in out.splitlines() if l.startswith("+") and not l.startswith("+++")]


def cmd_status(a):
    c = counts()
    dirty = [l[3:] for l in git("status", "--porcelain").stdout.splitlines()]
    if c is None:
        print("у ветки канона нет upstream — синхронизации нет")
        return 2
    print(f"канон: впереди GitHub на {c[0]}, позади на {c[1]}; незакоммиченных путей {len(dirty)}")
    for d in dirty[:20]:
        print("   " + d)
    return 0 if c == (0, 0) else 3


def cmd_pull(a):
    try:
        c = counts()
    except ConnectionError as e:
        print(f"канон: GitHub не ответил ({e}) — работаю с локальной копией, отправлю после")
        return 2
    if c is None:
        print("канон: у ветки нет upstream — синхронизации с GitHub нет")
        return 2
    ahead, behind = c
    note = ""
    if behind:
        r = git("pull", "--ff-only", "--quiet")
        if r.returncode:
            print(f"канон: позади GitHub на {behind}, впереди на {ahead} — сам не подтягиваю "
                  f"({r.stderr.strip()[:120]}); нужна команда `py -3 kos_sync.py push`")
            return 3
        note = f"подтянуто {behind} коммитов с GitHub"
    if ahead:
        print(f"канон: {note + '; ' if note else ''}не отправлено {ahead} коммитов — `py -3 \"{Path(__file__)}\" push`")
        return 3
    print(f"канон: синхронно с GitHub{' (' + note + ')' if note else ''}")
    return 0


def cmd_push(a):
    try:
        c = counts()
    except ConnectionError as e:
        print(f"[kos_sync] GitHub не ответил ({e}) — коммит остался локально, отправка при следующем коммите или "
              f"командой push")
        return 2
    if c is None:
        print("[kos_sync] у ветки нет upstream — отправлять некуда")
        return 2
    ahead, behind = c
    if behind:
        if tracked_dirty():
            print(f"[kos_sync] на GitHub {behind} новых коммитов, а в отслеживаемых файлах есть незакоммиченные правки "
                  f"(возможно, чужого чата) — перебазирование не запускаю; отправка отложена")
            return 3
        r = git("pull", "--rebase", "--quiet")
        if r.returncode:
            git("rebase", "--abort")
            print(f"[kos_sync] не удалось подтянуть с перебазированием ({r.stderr.strip()[:200]}) — нужна ручная работа")
            return 3
        ahead = int(git("rev-list", "--count", "@{u}..HEAD", check=True).stdout)
    if not ahead:
        print("[kos_sync] канон синхронен с GitHub")
        return 0
    found = scan_lines(added_lines("@{u}..HEAD"))
    if found:
        print(f"[kos_sync] ОТПРАВКА ОСТАНОВЛЕНА: в добавленных строках {len(found)} подозрительных фрагментов:")
        for cls, frag in found[:15]:
            print(f"   {cls}: {frag}")
        print("   Убрать из коммита (секреты — только в .env), затем `kos_sync.py push`.")
        return 1
    r = git("push", "--follow-tags", "--quiet")
    if r.returncode:
        print(f"[kos_sync] push не прошёл: {r.stderr.strip()[:300]}")
        return 2
    left = git("rev-list", "--count", "@{u}..HEAD", check=True).stdout.strip()
    print(f"[kos_sync] отправлено на GitHub коммитов: {ahead}; осталось неотправленных: {left}")
    return 0 if left == "0" else 2


def cmd_install(a):
    git("config", "core.hooksPath", HOOKS, check=True)
    print(f"core.hooksPath = {git('config', '--get', 'core.hooksPath').stdout.strip()}")
    return 0


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("pull")
    p.add_argument("--brief", action="store_true")
    p = sub.add_parser("push")
    p.add_argument("--hook", action="store_true", help="вызов из post-commit: код возврата всегда 0")
    sub.add_parser("install")
    a = ap.parse_args()
    try:
        rc = {"status": cmd_status, "pull": cmd_pull, "push": cmd_push, "install": cmd_install}[a.cmd](a)
    except Exception as e:                       # хук не должен ронять коммит или старт сессии
        print(f"[kos_sync] ошибка: {e}")
        rc = 2
    if getattr(a, "hook", False) or getattr(a, "brief", False):
        return 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
