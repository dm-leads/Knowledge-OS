"""Переносимый PostgreSQL 16 для контрактных тестов на этой машине (Docker нет, службу не ставим).
Бинарники — zip EDB без установщика. Лежат ВНЕ репозитория, в пути только из ASCII: %LOCALAPPDATA%\\datacore\\pgbin
(переопределяется DATACORE_PG_HOME). Причина: initdb падает на кириллице и длинном тире в пути
(«invalid byte sequence for encoding UTF8: 0x97», проверено 14.09.2026). Данные тестов — временная папка."""
from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PG_ZIP_URL = "https://get.enterprisedb.com/postgresql/postgresql-16.10-1-windows-x64-binaries.zip"
PG_ZIP_SHA256 = "ebb3b6af4fa69dea9951b66855bc4d42dc04e56ccb9aa7024ce3c58bd89d6b0c"   # снято 14.09.2026, 322 530 154 байта


def pg_home() -> Path:
    """Папка бинарников: DATACORE_PG_HOME или %LOCALAPPDATA%\\datacore\\pgbin. Путь обязан быть ASCII."""
    home = Path(os.environ.get("DATACORE_PG_HOME") or Path(os.environ["LOCALAPPDATA"]) / "datacore" / "pgbin")
    if not str(home).isascii():
        raise RuntimeError(f"путь к Postgres должен быть из ASCII-символов, иначе initdb падает: {home}")
    return home


def bin_dir_of(home: Path) -> Path:
    return home / "pgsql" / "bin"


def ensure_binaries(home: Path | None = None) -> Path:
    home = home or pg_home()
    bin_dir = bin_dir_of(home)
    if (bin_dir / "pg_ctl.exe").exists():
        return bin_dir
    home.mkdir(parents=True, exist_ok=True)
    archive = home / "pg16.zip"
    if not archive.exists():
        urllib.request.urlretrieve(PG_ZIP_URL, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if PG_ZIP_SHA256 and digest != PG_ZIP_SHA256:
        raise RuntimeError(f"sha256 архива Postgres не совпал: {digest}")
    with zipfile.ZipFile(archive) as z:
        z.extractall(home)
    return bin_dir


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PortablePostgres:
    def __init__(self, bin_dir: Path, data_dir: Path | None = None, user: str = "datacore"):
        self.bin = bin_dir
        self.data = data_dir or Path(tempfile.mkdtemp(prefix="datacore-pg-"))
        self.user = user
        self.port = free_port()

    def start(self) -> str:
        if not (self.data / "PG_VERSION").exists():
            # capture_output без text=True: initdb пишет в stderr в кодировке Windows, utf-8-декодер падает
            subprocess.run([str(self.bin / "initdb.exe"), "-D", str(self.data), "-U", self.user, "-A", "trust",
                            "-E", "UTF8", "--locale=C"], check=True, capture_output=True)
        # ⚠️ Вывод pg_ctl start НЕ перехватывать: сервер наследует канал, и run() не возвращается
        # (проверено 14.09.2026 — проба зависла на 5 минут). Лог сервера — в файл через -l.
        subprocess.run([str(self.bin / "pg_ctl.exe"), "-D", str(self.data), "-l", str(self.data / "log.txt"),
                        "-o", f"-p {self.port} -c listen_addresses=127.0.0.1", "-w", "start"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"postgresql://{self.user}@127.0.0.1:{self.port}/postgres"

    def stop(self) -> None:
        subprocess.run([str(self.bin / "pg_ctl.exe"), "-D", str(self.data), "-m", "fast", "-w", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(self.data, ignore_errors=True)
