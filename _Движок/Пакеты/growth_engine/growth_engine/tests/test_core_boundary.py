"""Граница ядра: core/ переносится в другую компанию без правок.

Три проверки: в тексте нет имён инстанса и сервисов; импортируются только разрешённые модули; нет
динамического кода, которым можно обойти список импортов. Данные приходят в ядро только через
адаптеры, которые передаёт вызывающий.
"""
import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
FORBIDDEN_WORDS = ("roistat", "lark", "amocrm", "amo_", "moysklad", "мойсклад", "yandex", "яндекс", "metrika",
                   "custom_", "breezeks", "бризекс", "atmeex", "55101", "234577", "sipuni", "wazzup")
# hashlib, json, unicodedata — чистые вычисления стандартной библиотеки (номер числа, ячейки хранилища): без сети,
# процессов, файлов и окружения, поэтому граница ядра не ослабляется.
ALLOWED_IMPORTS = {"__future__", "dataclasses", "datetime", "enum", "math", "re", "pathlib", "typing", "yaml",
                   "hashlib", "json", "unicodedata"}
FORBIDDEN_NAMES = {"__import__", "eval", "exec", "compile", "open", "importlib"}


def _core_trees():
    for path in sorted(CORE.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_core_has_no_instance_or_vendor_names():
    hits = []
    for path in sorted(CORE.rglob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        hits += [f"{path.name}: {word}" for word in FORBIDDEN_WORDS if word in text]
    assert hits == [], f"в ядре найдены имена инстанса или сервисов: {hits}"


def test_core_imports_only_allowed_modules():
    hits = []
    for path, tree in _core_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            hits += [f"{path.name}: {name}" for name in names if name.split(".")[0] not in ALLOWED_IMPORTS]
    assert hits == [], f"ядро импортирует неразрешённые модули: {hits}"


def test_core_has_no_dynamic_code():
    hits = [f"{path.name}: {node.id}" for path, tree in _core_trees() for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES]
    assert hits == [], f"в ядре динамический код или прямой ввод-вывод: {hits}"
