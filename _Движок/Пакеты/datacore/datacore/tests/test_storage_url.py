"""Выбор адреса хранилища.

Почему это важно: 23.09.2026 адрес базы передавался загрузчику аргументом `--db`, и пароль рабочей
базы оказался виден в списке процессов сервера — его пришлось отзывать. Переменная окружения
закрывает эту дыру, но только если её действительно читают — каждая команда ядра.
"""
import re
from pathlib import Path

from datacore.schema.config import load_config
from datacore.serve.storage import storage_url
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
PACKAGE = Path(__file__).resolve().parents[1]


def test_argument_wins(monkeypatch):
    monkeypatch.setenv(CFG.postgres_url_env, "postgresql://from-env/db")
    assert storage_url(CFG, "postgresql://explicit/db") == "postgresql://explicit/db"


def test_environment_is_used_when_no_argument(monkeypatch):
    monkeypatch.setenv(CFG.postgres_url_env, "postgresql://from-env/db")
    assert storage_url(CFG) == "postgresql://from-env/db"


def test_falls_back_to_duckdb_file(monkeypatch):
    monkeypatch.delenv(CFG.postgres_url_env, raising=False)
    assert storage_url(CFG).startswith("duckdb:///") and storage_url(CFG).endswith(".duckdb")


def test_empty_environment_is_not_an_address(monkeypatch):
    """Пустая переменная — не адрес: иначе прогон молча ушёл бы в пустую строку вместо базы."""
    monkeypatch.setenv(CFG.postgres_url_env, "")
    assert storage_url(CFG).startswith("duckdb:///")


def test_no_command_builds_address_bypassing_the_helper():
    """Каждая команда ядра обязана звать storage_url. Своя сборка адреса на сервере читала бы локальный файл
    вместо рабочей базы, а аргумент вернул бы адрес в командную строку — вместе с паролем. При переносе в пакет
    так нашлись `checks/known.py` и `checks/history.py` (28.09.2026)."""
    pattern = re.compile(r"a\.db\s+or\s+f?[\"']duckdb")
    hits = [p.relative_to(PACKAGE).as_posix() for folder in ("serve", "checks")
            for p in (PACKAGE / folder).glob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert hits == [], f"адрес базы собирается мимо storage_url: {hits}"
