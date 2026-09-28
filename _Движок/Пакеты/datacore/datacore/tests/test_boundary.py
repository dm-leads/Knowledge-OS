"""Граница пакета (Я5, К9; плейбук «Система с кодом», раздел 4): в коде пакета нет имён систем-источников, их
технических полей, имени компании, доменов и адресов; модели не читают raw мимо facts; в schema/ нет динамического кода.

Граница Ядра данных строже, чем у Движка роста: проверяется ВЕСЬ текст файла, включая комментарии и строки
документации. Переносимость утекает и через пояснения — в них пишется роль («CRM», «учётная система», «трекер»),
а не имя поставщика. Имена поставщиков живут только в загрузчиках и конфигурации инстанса, вне пакета.

Список запретов — здесь, в тесте: пакет не читает конфигурацию инстанса. Инстанс дополнительно прогоняет ту же
проверку со своим списком из конфигурации (идентификаторы полей, кабинеты) — `scan()` для этого и вынесена.
Тесты пакета проверяются только на имя компании: в тестовых данных допустимы метки поставщиков-заглушек.
"""
import ast
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
CODE_DIRS = ("schema", "models", "checks", "serve")
COMPANY = ("breezeks", "бризекс", "atmeex", "атмикс", "55101", "234577")
VENDOR = ("roistat", "ройстат", "amocrm", "amo_", "амоcrm", "moysklad", "мойсклад", "мой склад", "yandex", "яндекс",
          "metrika", "директ", "sipuni", "wazzup", "lark", "bitrix", "битрикс", "calltouch", "comagic", "salesbot",
          "custom_", "order_field_")
# Домен и адрес — тоже привязка к инстансу. Петля (127.0.0.1) — адрес по умолчанию для локальной СУБД, не инстанс.
ADDRESS = re.compile(r"\b[a-z0-9-]+\.(?:ru|рф|su|com|net|org|io)\b|\b(?!127\.0\.0\.1\b)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
ALLOWED_IMPORTS = {"__future__", "dataclasses", "datetime", "decimal", "enum", "math", "re", "hashlib", "hmac", "pathlib",
                   "typing", "urllib", "yaml", "duckdb", "psycopg", "pandas", "datacore"}
DYNAMIC = {"__import__", "eval", "exec", "compile", "importlib"}
SUFFIXES = (".py", ".sql", ".yaml", ".yml")


def code_files(root: Path = PACKAGE):
    """Файлы кода пакета: всё, кроме тестов, кэша и журналов движка моделей."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.suffix not in SUFFIXES or rel.parts[0] == "tests":
            continue
        if {".cache", "logs", "__pycache__"} & set(rel.parts):
            continue
        yield path


def scan(root: Path = PACKAGE, words=COMPANY + VENDOR) -> list[str]:
    """Найденные в коде пакета запрещённые имена и адреса: «файл: слово». Пусто — граница цела."""
    hits = []
    for path in code_files(root):
        text = path.read_text(encoding="utf-8").lower()
        rel = path.relative_to(root).as_posix()
        hits += [f"{rel}: {w}" for w in words if w.lower() in text]
        hits += [f"{rel}: адрес {m}" for m in ADDRESS.findall(text)]
    return hits


def test_no_vendor_company_or_address_in_package_code():
    assert scan() == [], "в коде пакета имена поставщиков, компании или адреса — место им в инстансе"


def test_package_tests_do_not_name_the_company():
    hits = []
    for path in sorted((PACKAGE / "tests").rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8").lower()
        hits += [f"{path.name}: {w}" for w in COMPANY if w in text]
    assert hits == [], f"тесты пакета называют компанию — берите метки b1, b2: {hits}"


def test_models_do_not_read_raw():
    hits = [p.relative_to(PACKAGE).as_posix() for p in code_files()
            if p.suffix == ".sql" and p.parent.name != "migrations" and "raw." in p.read_text(encoding="utf-8").lower()]
    assert hits == [], f"модели читают raw мимо facts (К9): {hits}"


def test_schema_imports_only_allowed_modules_and_no_dynamic_code():
    hits = []
    for path in (PACKAGE / "schema").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            elif isinstance(node, ast.Name) and node.id in DYNAMIC:
                hits.append(f"{path.name}: {node.id}")
                continue
            else:
                continue
            hits += [f"{path.name}: {n}" for n in names if n.split(".")[0] not in ALLOWED_IMPORTS]
    assert hits == [], f"schema/ импортирует неразрешённое или использует динамический код: {hits}"


@pytest.mark.parametrize("planted", [
    "SELECT 1 AS custom_41 FROM facts.deal",                    # техническое поле поставщика
    "-- выгрузка из МойСклад по счёту",                         # имя поставщика в комментарии
    "-- адрес сервера 10.1.2.3",                                # адрес
])
def test_planted_name_is_caught(tmp_path, planted):
    """Красный случай К9: подложенное в модель имя ловится, в том числе в комментарии. Подкладывается в копию
    раскладки во временной папке — выпуск в проекте тест не трогает (иначе краснел бы паспорт)."""
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "zz_planted.sql").write_text(planted, encoding="utf-8")
    assert scan(tmp_path) != []
