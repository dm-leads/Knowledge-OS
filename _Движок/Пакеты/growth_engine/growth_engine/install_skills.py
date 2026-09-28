"""Установка скиллов Движка роста — ссылкой (junction) на исходник в каноне Knowledge-OS, а не копией.

Исходник навыков шагов — один на устройство: `<Knowledge-OS>\\_Движок\\Исходники скиллов\\growth-*`; корень канона —
переменная KNOWLEDGE_OS, иначе `D:\\Knowledge-OS`. В папке навыков проекта и в глобальной папке пользователя ставятся
ссылки на него. До 28.09.2026 навыки ставились копией из репозитория инстанса, и другой проект получал их снимком на
день копирования: правка до него не доходила. Ссылка это закрывает — правка в каноне видна везде сразу.

Правила установки:
- ставятся только навыки `growth-*`; остальные исходники канона ставит бутстрап Knowledge-OS (шаг 2);
- ссылка на нужный исходник уже стоит — ничего не делается; повторный запуск ничего не меняет;
- на месте ссылки лежит обычная папка-копия — без `--replace-copies` это отказ с объяснением; с ключом копия
  отодвигается в резерв РЯДОМ с папкой навыков (`skills-резерв-ГГГГ-ММ-ДД`), а не внутрь: внутри её SKILL.md с тем же
  именем был бы виден дважды. Резерв удаляет человек — после того как навык открылся в новой сессии;
- ссылка ведёт в другое место — не трогается, это отказ.
Успех — «ссылки на месте» с числами и проверка, что через каждую ссылку читается исходник.

Запуск из Скрипты/:
  py -3 -m growth_engine.install_skills --project "C:/Users/redmi/Breezeks-Git/.claude/skills"
     --global "C:/Users/redmi/.claude/skills" [--replace-copies] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT_ENV = "KNOWLEDGE_OS"
DEFAULT_ROOT = Path("D:/Knowledge-OS")
PREFIX = "growth-"


def source_root() -> Path:
    """Папка исходников навыков в каноне этого устройства."""
    return Path(os.environ.get(ROOT_ENV) or DEFAULT_ROOT) / "_Движок" / "Исходники скиллов"


SOURCE = source_root()


def skill_dirs(folder: Path = SOURCE) -> list[Path]:
    """Папки навыков движка в исходнике: `growth-*` с файлом SKILL.md."""
    if not folder.is_dir():
        raise FileNotFoundError(f"исходника навыков нет: {folder}")
    found = sorted(path for path in folder.iterdir() if path.name.startswith(PREFIX) and (path / "SKILL.md").is_file())
    if not found:
        raise FileNotFoundError(f"в исходнике нет навыков {PREFIX}*: {folder}")
    return found


def link(where: Path, source: Path) -> None:
    """Ссылка на папку: junction на Windows (без прав администратора, между дисками), иначе символическая ссылка."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(source), str(where))
    else:
        os.symlink(source, where, target_is_directory=True)


def is_link_to(path: Path, source: Path) -> bool:
    """Путь — ссылка (junction или символическая), и ведёт она на этот исходник."""
    linked = os.path.isjunction(path) or path.is_symlink()
    return linked and os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(source))


def _state(where: Path, source: Path) -> str:
    if is_link_to(where, source):
        return "на месте"
    if os.path.isjunction(where) or where.is_symlink():
        return "чужая ссылка"
    if where.exists():
        return "копия"
    return "нет"


def reserve_dir(target: Path, today: date) -> Path:
    """Резерв для отодвинутых копий — рядом с папкой навыков, а не внутри неё."""
    return target.parent / f"{target.name}-резерв-{today:%Y-%m-%d}"


def install(folder: Path, targets: list[Path], replace_copies: bool = False, dry_run: bool = False,
            today=date.today) -> dict:
    """Ставит ссылки. Возвращает счётчики и список отказов; ошибку недоступного места поднимает вызывающему."""
    result = {"поставлено": 0, "на месте": 0, "отодвинуто": [], "отказы": []}
    for target in targets:
        for source in skill_dirs(folder):
            where = target / source.name
            state = _state(where, source)
            if state == "на месте":
                result["на месте"] += 1
                continue
            if state == "чужая ссылка":
                result["отказы"].append(f"{where}: ссылка ведёт не туда ({os.path.realpath(where)}) — не тронута")
                continue
            if state == "копия" and not replace_copies:
                result["отказы"].append(f"{where}: на месте лежит копия — чтобы отодвинуть её в резерв и поставить "
                                        "ссылку, запустите с --replace-copies")
                continue
            if dry_run:
                continue
            if state == "копия":
                reserve = reserve_dir(target, today())
                reserve.mkdir(parents=True, exist_ok=True)
                if (reserve / source.name).exists():
                    result["отказы"].append(f"{where}: в резерве {reserve} уже есть {source.name} — не тронуто")
                    continue
                shutil.move(str(where), str(reserve / source.name))
                result["отодвинуто"].append(reserve / source.name)
            target.mkdir(parents=True, exist_ok=True)
            link(where, source)
            result["поставлено"] += 1
    return result


def verify(folder: Path, targets: list[Path]) -> list[str]:
    """Расхождения: на месте не ссылка на исходник или через ссылку читается не исходник."""
    problems = []
    for target in targets:
        for source in skill_dirs(folder):
            where = target / source.name
            if not is_link_to(where, source):
                problems.append(f"не ссылка на исходник: {where}")
            elif (where / "SKILL.md").read_bytes() != (source / "SKILL.md").read_bytes():
                problems.append(f"через ссылку читается не исходник: {where}")
    return problems


def run(args, out=print, folder: Path | None = None, today=date.today) -> int:
    folder = folder or SOURCE
    targets = [Path(args.project), Path(args.__dict__["global"])]
    try:
        skills = skill_dirs(folder)
    except FileNotFoundError as exc:
        out(f"❌ ссылки не поставлены: {exc}")
        return 1
    out(f"Скиллы движка: {len(skills)} — {', '.join(path.name for path in skills)}; исходник {folder}")
    for target in targets:
        out(f"   место установки: {target}")
    dry_run = getattr(args, "dry_run", False)
    try:
        result = install(folder, targets, getattr(args, "replace_copies", False), dry_run, today)
    except OSError as exc:
        out(f"❌ установка прервана ({type(exc).__name__}) — ссылки могли встать не во все места")
        out("ИТОГ: ссылки НЕ подтверждены — повторите, когда место установки станет доступно")
        return 1
    for moved in result["отодвинуто"]:
        out(f"   копия отодвинута в резерв: {moved}")
    for refusal in result["отказы"]:
        out(f"❌ {refusal}")
    if dry_run:
        out("сухой прогон: ничего не изменено")
        return 0 if not result["отказы"] else 1
    problems = verify(folder, targets) if not result["отказы"] else []
    for problem in problems:
        out(f"❌ {problem}")
    ok = not result["отказы"] and not problems
    out(f"ИТОГ: ссылки {'на месте' if ok else 'НЕ подтверждены'} — поставлено {result['поставлено']}, уже на месте "
        f"{result['на месте']}, копий отодвинуто {len(result['отодвинуто'])}, проверено "
        f"{len(skills) * len(targets) if ok else 0}")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Навыки Движка роста — ссылкой на исходник в каноне Knowledge-OS")
    parser.add_argument("--project", required=True, help="папка скиллов проекта")
    parser.add_argument("--global", required=True, help="глобальная папка скиллов пользователя")
    parser.add_argument("--replace-copies", action="store_true",
                        help="копию на месте ссылки отодвинуть в резерв рядом с папкой навыков и поставить ссылку")
    parser.add_argument("--dry-run", action="store_true", help="показать план и ничего не менять")
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
