"""Политика ПДн (Я6, Р2): телефон и почта — только HMAC-хэш ключом инстанса; проверка текста перед записью (К6)."""
from __future__ import annotations

import hashlib
import hmac
import re

from .errors import RuleViolation

_DIGITS = re.compile(r"\D+")
# Телефон: код страны (+7 / 8 / 7) и десять цифр с любыми разделителями. ID сущностей без кода страны не совпадают.
_PHONE_IN_TEXT = re.compile(r"(?<!\d)(?:\+?7|8)[\s\-().]*\d{3}[\s\-().]*\d{3}[\s\-().]*\d{2}[\s\-().]*\d{2}(?!\d)")
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

MAX_TEXT_LEN = 200   # длиннее — не код и не значение справочника, а свободный текст: имена, адреса, диалоги не хранятся
EXACT_PHONE_COLUMNS = frozenset({"callee_line"})   # значение целиком — номер линии компании из белого списка, ничего другого

# Колонки, где лежит технический код документа источника, а не текст. Шаблон телефона на них не применяется: в
# идентификаторе вида «f8912345-6789-11f0-0a80-000000000001» кусок «8912345-6789» читается как телефон 8-912-345-67-89.
# Живой случай 16.09.2026: несколько таких совпадений на всю историю платежей остановили загрузку по К6.
# Это не ослабление правила: значение обязано быть кодом (_OPAQUE_ID), то есть текста, имени или адреса тут не будет.
# shipment_id и invoice_id добавлены 23.09.2026: этап 5 завёл их без этой отметки, и загрузка на сервере
# встала по К6 на номере отгрузки. Новая колонка с кодом документа — сюда же, в момент её появления.
OPAQUE_ID_COLUMNS = frozenset({"payment_id", "order_id", "shipment_id", "invoice_id"})   # строковые коды; visit_id и call_id — числа
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")


def normalize_phone(raw: str | None) -> str | None:
    if raw is None:
        return None
    digits = _DIGITS.sub("", str(raw))
    if not digits:
        return None
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def normalize_email(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    return value or None


def _hmac(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    if not key:
        raise ValueError("ключ ПДн пуст — задайте переменную окружения инстанса")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_phone(raw: str | None, key: bytes) -> str | None:
    return _hmac(normalize_phone(raw), key)


def hash_email(raw: str | None, key: bytes) -> str | None:
    return _hmac(normalize_email(raw), key)


def hash_identifier(raw: str | None, key: bytes) -> str | None:
    """Идентификатор посетителя (cookie счётчика) — псевдоним человека: хранится только хэшем ключа инстанса."""
    value = (str(raw).strip() if raw is not None else "") or None
    return _hmac(value, key)


def contains_foreign_pii(text: str, company_lines=frozenset()) -> bool:
    """Почта или телефон, который не является линией компании. Для очистки значений до записи в сырой слой."""
    if _EMAIL_IN_TEXT.search(text):
        return True
    return any(normalize_phone(m.group(0)) not in company_lines for m in _PHONE_IN_TEXT.finditer(text))


def find_pii(text: str) -> str | None:
    if _PHONE_IN_TEXT.search(text):
        return "телефон"
    if _EMAIL_IN_TEXT.search(text):
        return "почта"
    return None


def require_no_pii(rows: list[dict], allowed: dict[str, frozenset[str]] | None = None) -> None:
    """К6: ПДн открытым текстом в любой строковой колонке, кроме «*_hash». Почта запрещена везде. В колонках из
    `allowed` каждый телефон, найденный в тексте, обязан быть номером линии компании из белого списка (сравнение по
    нормализованному номеру), остальной текст проходит; в EXACT_PHONE_COLUMNS значение целиком обязано быть таким
    номером. Свободный текст длиннее MAX_TEXT_LEN отвергается: барьер против имён, адресов и цитат."""
    allowed = allowed or {}
    for i, row in enumerate(rows):
        for column, value in row.items():
            if column.endswith("_hash") or not isinstance(value, str):
                continue
            if len(value) > MAX_TEXT_LEN:
                raise RuleViolation("К6", f"строка {i}, колонка «{column}»: свободный текст ({len(value)} знаков) не хранится")
            if _EMAIL_IN_TEXT.search(value):
                raise RuleViolation("К6", f"строка {i}, колонка «{column}»: почта открытым текстом")
            if column in OPAQUE_ID_COLUMNS:
                # Код документа источника проверяется на форму, а не шаблоном телефона: цифры в идентификаторе
                # складываются в номер случайно. Форма при этом обязательна — иначе колонка станет лазейкой.
                if not _OPAQUE_ID.match(value):
                    raise RuleViolation("К6", f"строка {i}, колонка «{column}»: значение — не код документа источника")
                continue
            if column in allowed:
                if column in EXACT_PHONE_COLUMNS:
                    if normalize_phone(value) not in allowed[column]:
                        raise RuleViolation("К6", f"строка {i}, колонка «{column}»: значение — не номер линии компании")
                    continue
                foreign = [m.group(0) for m in _PHONE_IN_TEXT.finditer(value)
                           if normalize_phone(m.group(0)) not in allowed[column]]
                if foreign:
                    raise RuleViolation("К6", f"строка {i}, колонка «{column}»: телефон не из списка линий компании")
                continue
            if _PHONE_IN_TEXT.search(value):
                raise RuleViolation("К6", f"строка {i}, колонка «{column}»: телефон открытым текстом")
