"""Граница переносимого пакета (28.09.2026): тест границы ядра расширен на всё, что уедет в пакет движка.

Движок переезжает в пакет канона Knowledge-OS и подключается в проекты ссылкой. Тест `core/` стерёг только ядро, а в
пакет пойдут ещё хранилища, адаптеры и общие команды. Правила:
- имя компании не встречается в коде пакета нигде — ни в командах, ни в адаптерах, ни в хранилищах;
- имя системы (поставщика) допустимо только там, где ему место: адаптеры `sources/`, хранилища `storage/` и реестр
  адаптеров `adapters.py`; общие команды берут системы по ролям конфигурации и хранилище — через выбор хранилища.
Пояснения (строки документации, комментарии) не проверяются: имя поставщика в объяснении — не логика.
Модули первого инстанса (сцена приёмки, перенос карты v1.0, контрольный срез) с 28.09.2026 живут в инстансе, вне пакета.
"""
from pathlib import Path

import pytest

from growth_engine.boundary_report import split_code_and_prose

ENGINE = Path(__file__).resolve().parents[1]
COMPANY = ("breezeks", "бризекс", "atmeex", "55101", "234577")
VENDOR = ("roistat", "lark", "amocrm", "amo_", "moysklad", "мойсклад", "yandex", "яндекс", "metrika", "sipuni",
          "wazzup")
VENDOR_PLACES = ("storage", "sources")
VENDOR_MODULES = {"adapters.py"}


def package_modules():
    for path in sorted(ENGINE.rglob("*.py")):
        relative = path.relative_to(ENGINE)
        if relative.parts[0] in ("tests", "core") or path.name == "__init__.py":
            continue
        if "__pycache__" in relative.parts:
            continue
        yield relative


def code_of(relative: Path) -> str:
    code, _ = split_code_and_prose((ENGINE / relative).read_text(encoding="utf-8"))
    return code


@pytest.mark.parametrize("relative", list(package_modules()), ids=str)
def test_no_company_name_in_package_code(relative):
    found = [word for word in COMPANY if word in code_of(relative)]
    assert not found, f"{relative}: имя компании в коде пакета — {found}"


@pytest.mark.parametrize("relative", [m for m in package_modules()
                                      if m.parts[0] not in VENDOR_PLACES and m.name not in VENDOR_MODULES], ids=str)
def test_vendor_names_stay_in_adapters_and_storage(relative):
    found = [word for word in VENDOR if word in code_of(relative)]
    assert not found, (f"{relative}: имя системы в общей команде — {found}; систему брать по роли конфигурации, "
                       "хранилище — через выбор хранилища")
