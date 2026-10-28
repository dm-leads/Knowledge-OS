"""Протокол планировщика и общее: имена своих заданий, команда задания, запуск внешних программ.

Свои задания — по префиксу `KOS-content-engine-<id инстанса>-<job>`: чужие не попадают ни в план, ни в снятие.
Внешние программы (`schtasks`, `crontab`) запускаются через `runner` — в тестах подменяется. Команда задания —
`<python> -m content_engine <command> --profile <абсолютный путь> --unattended` из папки выпуска проекта (родитель
`content_engine/`), чтобы работал код выпуска проекта, а не установленный пакет.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PREFIX = "KOS-content-engine"


@dataclass
class Job:
    name: str            # полное имя задания
    command: str         # строка запуска
    workdir: str
    every: str           # day | week
    at: str              # ЧЧ:ММ
    weekday: str         # mon … sun или ""


def run_program(args: list[str], *, input_text: str | None = None, encoding: str = "utf-8") -> tuple[int, str]:
    done = subprocess.run(args, capture_output=True, text=True, encoding=encoding, errors="replace",
                          input=input_text)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def instance_id(instance) -> str:
    """Короткий латинский id инстанса: путь профиля с кириллицей в имя задания не идёт."""
    return hashlib.sha1(str(Path(instance.profile_path).resolve()).encode("utf-8")).hexdigest()[:8]


def release_dir(instance) -> Path:
    """Папка, где лежит выпуск пакета, которым запущена команда (родитель `content_engine/`)."""
    import content_engine
    return Path(content_engine.__file__).resolve().parent.parent


def jobs(instance) -> list[Job]:
    s = instance.p["schedule"]
    prof = str(Path(instance.profile_path).resolve())
    out = []
    for j in s["jobs"]:
        cmd = f'{s["python"]} -m content_engine {j["command"]} --profile "{prof}" --unattended'
        out.append(Job(f"{PREFIX}-{instance_id(instance)}-{j['name']}", cmd, str(release_dir(instance)),
                       j["every"], j["at"], j["weekday"]))
    return out


def own(name: str, instance) -> bool:
    return name.startswith(f"{PREFIX}-{instance_id(instance)}-")


def safe_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9-]+", name))
