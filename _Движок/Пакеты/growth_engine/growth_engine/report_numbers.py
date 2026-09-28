"""Сверка чисел отчёта с выводом команд (этап 7, задача 7.3): число без строки-источника — стоп.

Ворота скиллов проверяются кодом, а не рассказом: агент проходит шаги цикла, вывод каждой команды сохраняется в журнал
прогона, а этот скрипт сверяет отчёт с журналом. Каждое число отчёта обязано встречаться в выводе команд; число, которого
там нет, — выдумано или посчитано в тексте, и это стоп (страж 9: молчаливое допущение запрещено).

Что числом не считается: даты (`01.08.2026`, `2026-09-14`), номера стражей и шагов («страж 13», «шаг 2»), номера версий
(`v1.0`), номера артефактов (`D-v1-H1`, `H-001`), а также номера в именах файлов и путей (`01_health.txt`,
`01 — Проекты`) и ссылки на разделы (`§7.1`). Иначе сверка заставляла бы переименовывать журналы прогона вместо того,
чтобы ловить выдуманные числа. Знак числа — часть числа: `-500` и `500` разные. Разделителями тысяч
считаются все пробелы, которыми печатают числа, включая неразрывный и тонкий; десятичная запятая приводится к точке.
Поэтому «127 842» в отчёте и «127842» в выводе — одно число, а «1 234», собранное из чужих «1» и «234», — не найдено.

Ключи командной строки числами не считаются: `py -3`, `--months 3`, `--horizons 30,60,90` — это части команды.

Журналом считается любой файл, который команда напечатала или создала: если команда кладёт числа в файл отчёта по
ключу `--out` (мост, когорта, аддитивность печатают в поток только сводку), этот файл тоже подаётся ключом `--log`,
иначе настоящие числа отчёта окажутся «без источника».

Запуск из Скрипты/:
  py -3 -m growth_engine.report_numbers --report <отчёт.md> --log <вывод команд.txt> [--log <файл отчёта команды.md>]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

THIN = "    ⁠"      # пробелы-разделители тысяч: тонкий, узкий неразрывный, неразрывный и прочие
MINUS = "-−‒–"                 # знак минуса: дефис и типографские минусы
NUMBER = re.compile(rf"(?<![\w.,])[{MINUS}]?\d[\d{THIN} ]*(?:[.,]\d+)?(?![\w.,]*\d)")
DATE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{4}-\d{2}\b")
# Граница слова обязательна: без неё «конверсия 3,83%» съедается веткой «версия» и число исчезает из сверки.
LABELLED = re.compile(r"\b(?:страж|шаг|версия|v)\s*\d+(?:\.\d+)?|\b[A-ZА-Я]-[\w-]*\d[\w-]*", re.I)
# Имена файлов с номером, номера внутри путей и ссылки на разделы: это ярлыки, а не значения. Без этого правила сверка
# заставляла бы переименовывать журналы прогона (приёмочный прогон 15.09.2026).
LABELS = re.compile(r"\b\d+[_-][\w.-]+|[\\/][^\s\\/]*\d[^\s\\/]*|§\s*\d+(?:\.\d+)*|\b\d+\s+—\s+\w")
# Ключи командной строки — часть команды, а не значение: `--months 3`, `--horizons 30,60,90`, а также ключ
# интерпретатора `py -3`. Скиллы требуют приводить строки запуска дословно, поэтому без этого правила сверка не
# принимала бы ни один честный отчёт. Минус вне ключа остаётся числом: «сдвиг -500» — это −500.
KEYS = re.compile(r"(?:^|\s)--[A-Za-z][\w-]*(?:[ =]\S+)?|\bpy\s+-\d+\b")
SEPARATORS = str.maketrans("", "", " " + THIN)


def canonical(text: str) -> str:
    """Число в едином виде: знак сохраняется, разделители тысяч убраны, дробная часть через точку, без хвостовых нулей."""
    value = text.translate(SEPARATORS).replace(",", ".")
    sign = "-" if value[:1] in MINUS else ""
    value = value.lstrip(MINUS)
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    value = value or "0"
    return value if value == "0" else sign + value


def numbers(text: str) -> list[str]:
    """Числа текста без дат, номеров стражей и шагов, версий, номеров артефактов, имён файлов, путей и ссылок на
    разделы."""
    masked = LABELLED.sub(" ", LABELS.sub(" ", KEYS.sub(" ", DATE.sub(" ", text))))
    return [canonical(match.group()) for match in NUMBER.finditer(masked)]


def check(report: str, logs: str) -> list[str]:
    """Числа отчёта, которых нет в выводе команд, по порядку первого появления."""
    known = set(numbers(logs))
    missing, seen = [], set()
    for value in numbers(report):
        if value not in known and value not in seen:
            seen.add(value)
            missing.append(value)
    return missing


def run(args, out=print) -> int:
    report_path = Path(args.report)
    if not report_path.is_file():
        out(f"❌ сверка не выполнена: отчёта нет — {report_path}")
        return 1
    logs, absent = [], []
    for name in args.log:
        path = Path(name)
        (logs if path.is_file() else absent).append(path)
    if absent:
        out("❌ сверка не выполнена: нет журналов прогона — " + ", ".join(str(path) for path in absent))
        return 1
    if not logs:
        out("❌ сверка не выполнена: не указан ни один журнал прогона")
        return 1
    report = report_path.read_text(encoding="utf-8")
    joined = "\n".join(path.read_text(encoding="utf-8") for path in logs)
    total = numbers(report)
    missing = check(report, joined)
    out(f"Сверка чисел отчёта «{report_path.name}»: чисел в отчёте {len(total)}, журналов прогона {len(logs)}")
    for value in missing:
        out(f"   ❌ нет в выводе команд: {value}")
    if missing:
        out(f"ИТОГ: чисел без источника {len(missing)} — отчёт не принят")
        return 1
    out(f"ИТОГ: каждое число отчёта найдено в выводе команд — проверено {len(total)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сверка чисел отчёта о прогоне с выводом команд движка")
    parser.add_argument("--report", required=True, help="отчёт агента о прогоне шагов")
    parser.add_argument("--log", required=True, action="append",
                        help="файл с выводом команды или файл отчёта, созданный командой по --out; ключ повторяется")
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
