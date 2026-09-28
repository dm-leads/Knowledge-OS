"""Подключение проекта к канону Knowledge-OS (ADR-0004, 28.09.2026): блок в CLAUDE.md, ссылка `.kos`, исключение из git.

Проект может лежать где угодно: внутри канона, в репозитории компании, в любой папке диска. Подключение делает три
вещи, и повторный запуск ничего не дублирует:
- ставит в корне проекта ссылку-переход `.kos` на `_Движок/Подключение/` канона. Через неё `CLAUDE.md` проекта
  импортирует правила агента (`@.kos/agent-rules.md`) — без копии, правка в каноне сразу видна во всех чатах
  проекта. Импорт — через ссылку внутри проекта, потому что Claude Code не импортирует пути с кириллицей и не
  грузит внешние импорты без подтверждения (проба 28.09.2026);
- пишет в `CLAUDE.md` проекта блок «Канон Knowledge-OS» между метками `kos:begin` и `kos:end`: где канон, где
  методологии, реестр инструментов и пакеты, строка «канон сверен: <дата>». Текст проекта вне блока не трогается,
  правки внутри блока затираются. Дата сверки сохраняется, пока её не обновят ключом `--verified`;
- если проект в git-репозитории, исключает `.kos/` из git локально (`.git/info/exclude`), не трогая общий
  `.gitignore`: ссылка — особенность устройства, в репозиторий компании она не идёт.

Запуск (из любой папки):
  py -3 kos_connect.py --project <папка проекта> [--canon D:/Knowledge-OS] [--verified ГГГГ-ММ-ДД] [--no-rules-import]
Код возврата 1 — подключение невозможно; печатается причина.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

BEGIN = "<!-- kos:begin · блок подключения к канону Knowledge-OS · пишет kos_connect.py, правки внутри блока затираются -->"
END = "<!-- kos:end -->"
LINK = ".kos"
RULES = "agent-rules.md"
VERIFIED = "канон сверен: "


class ConnectError(Exception):
    """Подключение невозможно: причина — текстом для человека."""


@dataclass
class Report:
    passport: Path
    block_lines: int
    link: str
    git_exclude: str
    verified: str


def default_canon() -> Path:
    return Path(os.environ.get("KNOWLEDGE_OS", "D:/Knowledge-OS"))


def block(canon: Path, verified: str, rules_import: bool) -> str:
    """Блок подключения: импорт правил агента (если включён), пути канона, дата сверки."""
    root = str(canon).replace("/", "\\")
    lines = [BEGIN, "## Канон Knowledge-OS", ""]
    if rules_import:
        lines += [f"@{LINK}/{RULES}", ""]
    lines += [f"- Канон: `{root}`. Методологии — `Методологии\\_manifest.md`, реестр инструментов —",
              "  `Экосистема инструментов\\_manifest.md`, системы с кодом — `_Движок\\Пакеты\\_manifest.md`,",
              "  сценарии — `_Движок\\Сценарии\\_manifest.md`. Всё — ссылкой, не копией.",
              f"- {VERIFIED}{verified}",
              f"- Сверка перед задачей на данные, рост, гипотезы, инструменты: `git -C \"{root}\" log "
              f"--since={verified} --name-only`; применимое назвать, дату обновить (`kos_connect.py --verified`).",
              "- Урок, полезный другим проектам, — обобщить без данных компании и поднять в канон.",
              END]
    return "\n".join(lines)


def current_verified(text: str) -> str | None:
    """Дата сверки из существующего блока — чтобы повторный запуск её не сдвигал."""
    if BEGIN not in text or END not in text:
        return None
    inside = text[text.index(BEGIN):text.index(END)]
    for line in inside.splitlines():
        if VERIFIED in line:
            return line.split(VERIFIED, 1)[1].strip()
    return None


def write_block(passport: Path, new_block: str) -> None:
    """Блок заменяется на месте; нет блока — дописывается в конец; нет файла — создаётся."""
    text = passport.read_text(encoding="utf-8") if passport.is_file() else ""
    if BEGIN in text and END in text:
        start, end = text.index(BEGIN), text.index(END) + len(END)
        text = text[:start] + new_block + text[end:]
    else:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + new_block + "\n"
    with open(passport, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def ensure_link(project: Path, target: Path) -> str:
    """Ссылка-переход `.kos` на папку подключения канона; обычную папку с таким именем не трогаем."""
    link = project / LINK
    if os.path.isjunction(link) or link.is_symlink():
        if Path(os.path.realpath(link)) == Path(os.path.realpath(target)):
            return "уже есть"
        raise ConnectError(f"{link} ведёт не в канон ({os.path.realpath(link)}) — проверьте и удалите ссылку вручную")
    if link.exists():
        raise ConnectError(f"{link} — обычная папка, не ссылка на канон; переименуйте её, подключение её не трогает")
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    return "создана"


def exclude_from_git(project: Path) -> str:
    """`.kos/` — в `.git/info/exclude` репозитория проекта, один раз; общий `.gitignore` не трогаем."""
    result = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-toplevel", "--git-dir"],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        return "не репозиторий"
    top, git_dir = result.stdout.strip().splitlines()[:2]
    git_dir = Path(git_dir) if Path(git_dir).is_absolute() else project / git_dir
    relative = project.resolve().relative_to(Path(top).resolve()).as_posix()
    pattern = f"/{relative}/{LINK}/" if relative != "." else f"/{LINK}/"
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if pattern in text.splitlines():
        return "уже исключена"
    with open(exclude, "a", encoding="utf-8", newline="") as f:
        f.write(("" if not text or text.endswith("\n") else "\n") + pattern + "\n")
    return "исключена"


def connect(project: Path, canon: Path, today=None, verified: date | None = None,
            rules_import: bool = True) -> Report:
    project, canon = Path(project), Path(canon)
    today = today or date.today()
    connection = canon / "_Движок" / "Подключение"
    if not (connection / RULES).is_file():
        raise ConnectError(f"в каноне нет {connection / RULES} — проверьте путь к канону (--canon или KNOWLEDGE_OS)")
    if not project.is_dir():
        raise ConnectError(f"папки проекта нет: {project}")
    passport = project / "CLAUDE.md"
    text = passport.read_text(encoding="utf-8") if passport.is_file() else ""
    stamp = verified.isoformat() if verified else (current_verified(text) or today.isoformat())
    link, git_exclude = "не нужна", "не нужно"
    if rules_import:
        link = ensure_link(project, connection)
        git_exclude = exclude_from_git(project)
    new_block = block(canon, stamp, rules_import)
    write_block(passport, new_block)
    return Report(passport, new_block.count("\n") + 1, link, git_exclude, stamp)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Подключить проект к канону Knowledge-OS")
    parser.add_argument("--project", required=True, help="корень проекта — папка, где открывают чаты")
    parser.add_argument("--canon", default=None, help="корень канона (по умолчанию KNOWLEDGE_OS или D:/Knowledge-OS)")
    parser.add_argument("--verified", default=None, help="дата сверки с каноном ГГГГ-ММ-ДД (обновить строку)")
    parser.add_argument("--no-rules-import", action="store_true",
                        help="не импортировать правила агента в проект (если они уже грузятся глобально)")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    try:
        report = connect(Path(args.project), Path(args.canon) if args.canon else default_canon(),
                         verified=date.fromisoformat(args.verified) if args.verified else None,
                         rules_import=not args.no_rules_import)
    except (ConnectError, ValueError) as exc:
        print(f"❌ {exc}")
        return 1
    print(f"паспорт: {report.passport}; блок «Канон Knowledge-OS»: строк {report.block_lines}")
    print(f"ссылка .kos: {report.link}; исключение из git: {report.git_exclude}; канон сверен: {report.verified}")
    print("ИТОГ: проект подключён — новые чаты в этой папке получат правила агента и пути канона")
    return 0


if __name__ == "__main__":
    sys.exit(main())
