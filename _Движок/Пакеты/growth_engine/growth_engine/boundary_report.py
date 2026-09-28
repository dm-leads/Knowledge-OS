"""Отчёт о границе движка за пределами `core/`: где в хранилищах, адаптерах и командах стоят имена компании и систем.

Зачем (28.09.2026): движок переезжает в переносимый пакет. Тест границы стережёт только `core/`, а в пакет пойдут ещё
хранилища и общие команды — их надо проверить тем же списком слов, прежде чем решать, что общее, а что инстанса.
Команда только читает и печатает; строки документации и комментарии считаются отдельно от кода: имя поставщика в
пояснении — не то же, что имя в логике.

Запуск из Скрипты/:
  py -3 -m growth_engine.boundary_report
"""
from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from pathlib import Path

from .tests.test_core_boundary import FORBIDDEN_WORDS as FORBIDDEN

ENGINE = Path(__file__).resolve().parent
PARTS = ("storage", "sources", ".")


def split_code_and_prose(text: str) -> tuple[str, str]:
    """Код без строк документации и комментариев — и отдельно сами пояснения."""
    docs = set()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                docs.update(range(first.lineno, first.end_lineno + 1))
    code, prose = [], []
    comments = {}
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            comments[token.start[0]] = token.string
    for number, line in enumerate(text.splitlines(), 1):
        if number in docs:
            prose.append(line)
            continue
        comment = comments.get(number)
        if comment:
            prose.append(comment)
            line = line.replace(comment, "")
        code.append(line)
    return "\n".join(code).lower(), "\n".join(prose).lower()


def report() -> list[tuple[str, dict, dict]]:
    rows = []
    for part in PARTS:
        for path in sorted((ENGINE / part).glob("*.py")):
            if path.name == "__init__.py" or path.name == Path(__file__).name:
                continue
            code, prose = split_code_and_prose(path.read_text(encoding="utf-8"))
            in_code = {w: code.count(w) for w in FORBIDDEN if w in code}
            in_prose = {w: prose.count(w) for w in FORBIDDEN if w in prose}
            if in_code or in_prose:
                rows.append((str(path.relative_to(ENGINE)).replace("\\", "/"), in_code, in_prose))
    return rows


def main(argv=None) -> int:
    # Кодировка чинится ДО разбора аргументов: `--help` печатается внутри `parse_args()` (контракт команд движка).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argparse.ArgumentParser(prog="growth_engine.boundary_report", description=__doc__.splitlines()[0]).parse_args(argv)
    rows = report()
    fmt = lambda found: ", ".join(f"{word}×{count}" for word, count in sorted(found.items(), key=lambda x: -x[1])) or "—"
    for name, in_code, in_prose in rows:
        print(f"{name:30} в коде: {fmt(in_code):45} в пояснениях: {fmt(in_prose)}")
    clean_code = sum(1 for _, in_code, _ in rows if not in_code)
    print(f"ИТОГ: модулей с именами {len(rows)}; из них только в пояснениях {clean_code}, в коде {len(rows) - clean_code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
