"""Граница переносимого пакета (Т1.2): в пакете нет имён, доменов, путей и данных компаний-потребителей.

Строже образца: проверяются ВСЕ файлы пакета — код, тесты, фикстуры, шаблоны, данные, комментарии и строки
документации; пропускается только этот файл (в нём список запретов). Урок в пояснениях пишется словами, без чисел
первого потребителя: числа с пробелом-разделителем разрядов и «млн» запрещены — в коде числа пишутся слитно.
Имена поставщиков (Keys.so, Search Console, Вебмастер) запретом не считаются: это адаптеры пакета.
"""
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SKIPPED_DIRS = {"__pycache__", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".txt", ".json", ".md", ".csv", ".toml", ".html"}

FORBIDDEN = (
    "breezeks", "бризекс", "xn--90aifdm6al", "atmeex", "атмикс", "бризер", "breezer", "brizer",
    "tion.ru", "тион",
)
PATTERNS = {
    "кириллический домен .рф": re.compile(r"[а-яё0-9-]+\.рф", re.IGNORECASE),
    "число с пробелом-разделителем разрядов": re.compile(r"(?<![\d.,])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?![\d])"),
    "«млн»": re.compile(r"\bмлн\b", re.IGNORECASE),
    "путь рабочей папки компании": re.compile(r"Breezeks", re.IGNORECASE),
}


def package_files():
    for path in sorted(PACKAGE.rglob("*")):
        relative = path.relative_to(PACKAGE)
        if not path.is_file() or path.resolve() == SELF or SKIPPED_DIRS.intersection(relative.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            yield relative


def violations(text: str) -> list[str]:
    low = text.lower()
    found = [word for word in FORBIDDEN if word in low]
    found += [f"{name}: «{m.group(0)}»" for name, rx in PATTERNS.items() for m in [rx.search(text)] if m]
    return found


@pytest.mark.parametrize("relative", list(package_files()), ids=str)
def test_no_company_data_in_package(relative):
    found = violations((PACKAGE / relative).read_text(encoding="utf-8", errors="replace"))
    assert not found, f"{relative}: данные компании в пакете — {found}; вернуть в инстанс"


def test_detector_catches_planted_lines():
    assert violations("адрес https://пример.рф/blog")
    assert violations("сделок 2 889 вместо 2 893")
    assert violations("выручка 3 млн")
    assert not violations("порог 3000 объектов, домен example.ru, версия 1.0.0, 0.70")


# Выпуск уходит в GitLab компании: там у каждого `.md` проверяется шапка документа, а поиск секретов срабатывает на
# имя файла (урок выпуска 1.0.1). Поэтому в каталоге кода нет `.md` — шаблоны заданий хранятся `.txt` или строками,
# а фикстуры-статьи тест создаёт во временной папке; в именах файлов нет слов-примет секрета. Слово ищется целиком
# по частям имени: «keysso» — имя поставщика, а не ключ.
SECRET_WORDS = {"secret", "secrets", "token", "tokens", "key", "keys", "credential", "credentials", "password",
                "passwords"}


def name_problems(relative: Path) -> list[str]:
    problems = []
    if relative.suffix.lower() == ".md":
        problems.append("файл .md в каталоге кода")
    parts = {part for part in re.split(r"[^a-z0-9]+", relative.name.lower()) if part}
    if parts & SECRET_WORDS or relative.suffix.lower() in (".key", ".pem"):
        problems.append(f"имя похоже на секрет: {sorted(parts & SECRET_WORDS) or relative.suffix}")
    return problems


def all_files():
    for path in sorted(PACKAGE.rglob("*")):
        relative = path.relative_to(PACKAGE)
        if path.is_file() and not SKIPPED_DIRS.intersection(relative.parts):
            yield relative


def test_no_md_and_no_secret_like_names_in_package():
    found = {str(r): name_problems(r) for r in all_files() if name_problems(r)}
    assert not found, f"файлы, которые не пройдут проверки GitLab компании: {found}"


def test_name_detector_catches_planted_files():
    assert name_problems(Path("templates/task_brief.md"))
    assert name_problems(Path("core/token_store.py"))
    assert name_problems(Path("data/api-key.txt"))
    assert not name_problems(Path("adapters/keysso.py"))
    assert not name_problems(Path("steps/known_answers.py"))
