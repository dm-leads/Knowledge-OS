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
  `.gitignore`: ссылка — особенность устройства, в репозиторий компании она не идёт;
- заводит журнал действий и базу инцидентов из шаблонов канона, если их нет, и пишет пути к ним в блок
  («Стандарт — Три файла проекта», 28.09.2026). Существующие файлы не трогаются. У проекта свои файлы — пути к ним
  передаются ключами `--journal` и `--incidents` и дальше хранятся в блоке.

Запуск (из любой папки):
  py -3 kos_connect.py --project <папка проекта> [--canon D:/Knowledge-OS] [--verified ГГГГ-ММ-ДД] [--no-rules-import]
                       [--journal <папка или файл>] [--incidents <файл>]
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
JOURNAL_LINE = "- Журнал действий: `"
INCIDENTS_LINE = "- База инцидентов: `"
DEFAULT_JOURNAL = "0 — System Docs\\Журнал действий\\"
DEFAULT_INCIDENTS = "0 — System Docs\\Справочник — База инцидентов.md"
MONTH_FILE = "Журнал — {:%Y-%m}.md"
CANDIDATE_WORDS = ("журнал действий", "инцидент")
SKIP_DIRS = {"node_modules", "__pycache__", "venv"}
JOURNAL_TEMPLATE = "Шаблон — Журнал действий.md"
INCIDENTS_TEMPLATE = "Шаблон — База инцидентов.md"


class ConnectError(Exception):
    """Подключение невозможно: причина — текстом для человека."""


@dataclass
class Report:
    passport: Path
    block_lines: int
    link: str
    git_exclude: str
    verified: str
    journal: str = "уже есть"
    incidents: str = "уже есть"


def default_canon() -> Path:
    return Path(os.environ.get("KNOWLEDGE_OS", "D:/Knowledge-OS"))


GLOBAL_IMPORT = f"@kos/{RULES}"


def global_passport_path() -> Path:
    """Глобальный CLAUDE.md устройства (переменная KOS_GLOBAL_PASSPORT — для тестов и других устройств)."""
    given = os.environ.get("KOS_GLOBAL_PASSPORT")
    return Path(given) if given else Path.home() / ".claude" / "CLAUDE.md"


def rules_imported_globally(passport: Path) -> bool:
    """Правила канона уже грузятся во все чаты устройства из глобального CLAUDE.md."""
    return passport.is_file() and GLOBAL_IMPORT in passport.read_text(encoding="utf-8")


def block(canon: Path, verified: str, rules_import: bool, journal: str = DEFAULT_JOURNAL,
          incidents: str = DEFAULT_INCIDENTS) -> str:
    """Блок подключения: импорт правил агента (если нужен), три файла проекта, пути канона, дата сверки."""
    root = str(canon).replace("/", "\\")
    lines = [BEGIN, "## Канон Knowledge-OS", ""]
    if rules_import:
        lines += [f"@{LINK}/{RULES}", ""]
    else:
        lines += ["- Правила работы агента канона грузятся глобально: `~/.claude/CLAUDE.md` импортирует",
                  f"  `{GLOBAL_IMPORT}` (ссылка `~/.claude/kos` → `_Движок\\Подключение\\`)."]
    lines += ["- Три файла проекта (`Методологии\\Стандарт — Три файла проекта (паспорт, журнал действий, база "
              "инцидентов).md`):",
              "  этот паспорт — особенности проекта и правила работы с ним дописываются вне блока;",
              f"{JOURNAL_LINE}{journal}` — в начале чата прочитать последние записи, после блока работы дописать строку;",
              f"{INCIDENTS_LINE}{incidents}` — читать до гипотез о причине сбоя и перед шагом, который уже ломался;",
              "  после разбора — запись: симптом, причина, класс, защита.",
              f"- Канон: `{root}`. Методологии — `Методологии\\_manifest.md`, реестр инструментов —",
              "  `Экосистема инструментов\\_manifest.md`, системы с кодом — `_Движок\\Пакеты\\_manifest.md`,",
              "  сценарии — `_Движок\\Сценарии\\_manifest.md`. Всё — ссылкой, не копией.",
              f"- {VERIFIED}{verified}",
              f"- Сверка перед задачей на данные, рост, гипотезы, инструменты: `git -C \"{root}\" log "
              f"--since={verified} --name-only`; применимое назвать, дату обновить (`kos_connect.py --verified`).",
              "- Урок, полезный другим проектам, — обобщить без данных компании и поднять в канон.",
              END]
    return "\n".join(lines)


def block_value(text: str, marker: str) -> str | None:
    """Значение строки из существующего блока — чтобы повторный запуск его не сдвигал."""
    if BEGIN not in text or END not in text:
        return None
    inside = text[text.index(BEGIN):text.index(END)]
    for line in inside.splitlines():
        if marker in line:
            value = line.split(marker, 1)[1]
            return value.split("`", 1)[0].strip() if marker.endswith("`") else value.strip()
    return None


def current_verified(text: str) -> str | None:
    """Дата сверки из существующего блока."""
    return block_value(text, VERIFIED)


def shown(project: Path, given, folder_allowed: bool) -> str:
    """Путь для блока: внутри проекта — относительный, снаружи — абсолютный; папка — с `\\` в конце.

    Журнал (`folder_allowed`) — папка помесячных файлов или файл: несуществующий путь без расширения или с `\\` в
    конце — папка. База инцидентов — только файл `.md`.
    """
    raw = str(given)
    folder_hint = raw.endswith(("\\", "/"))
    path = Path(raw.replace("\\", "/"))
    path = (path if path.is_absolute() else project / path).resolve()
    try:
        text = str(path.relative_to(project.resolve()))
    except ValueError:
        text = str(path)
    text = text.replace("/", "\\")
    if not folder_allowed:
        if path.is_dir() or folder_hint or (not path.exists() and path.suffix.lower() != ".md"):
            raise ConnectError(f"база инцидентов — файл .md, а не папка: {raw}")
        return text
    is_folder = path.is_dir() or (not path.exists() and (folder_hint or not path.suffix))
    return text + "\\" if is_folder else text


def candidates(project: Path, depth: int = 4) -> list[Path]:
    """Файлы и папки проекта, похожие на уже заведённые журнал или базу инцидентов."""
    found, root_depth = [], len(project.parts)
    for folder, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        if len(Path(folder).parts) - root_depth >= depth:
            dirs[:] = []
        for name in dirs + files:
            if any(word in name.lower() for word in CANDIDATE_WORDS):
                found.append(Path(folder) / name)
    return found


def resolve(project: Path, shown_path: str) -> Path:
    path = Path(shown_path.rstrip("\\/").replace("\\", "/"))
    return path if path.is_absolute() else project / path


def from_template(template: Path, target: Path, project: Path, canon: Path, today: date) -> None:
    text = template.read_text(encoding="utf-8")
    for placeholder, value in (("<проект>", project.name), ("<ГГГГ-ММ>", f"{today:%Y-%m}"),
                               ("<канон>", str(canon).replace("/", "\\")), ("<дата>", today.isoformat())):
        text = text.replace(placeholder, value)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def ensure_journal(project: Path, shown_path: str, template: Path, canon: Path, today: date) -> str:
    """Журнал — папка помесячных файлов или один файл. Есть — не трогаем; нет — заводим из шаблона."""
    path = resolve(project, shown_path)
    if path.exists():
        return "уже есть"
    target = path / MONTH_FILE.format(today) if shown_path.endswith("\\") else path
    from_template(template, target, project, canon, today)
    return "создан"


def ensure_incidents(project: Path, shown_path: str, template: Path, canon: Path, today: date) -> str:
    path = resolve(project, shown_path)
    if path.exists():
        return "уже есть"
    from_template(template, path, project, canon, today)
    return "создана"


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
            rules_import: bool | None = None, global_passport: Path | None = None,
            journal=None, incidents=None) -> Report:
    """`rules_import=None` — решить самому: импорт в проект нужен, только если глобальный CLAUDE.md его не делает.

    `journal`, `incidents` — свои файлы проекта (путь от корня проекта или абсолютный); не заданы — берутся из
    существующего блока, а при первом подключении — пути по умолчанию в `0 — System Docs\\`.
    """
    project, canon = Path(project), Path(canon)
    today = today or date.today()
    if rules_import is None:
        rules_import = not rules_imported_globally(Path(global_passport) if global_passport
                                                   else global_passport_path())
    connection = canon / "_Движок" / "Подключение"
    if not (connection / RULES).is_file():
        raise ConnectError(f"в каноне нет {connection / RULES} — проверьте путь к канону (--canon или KNOWLEDGE_OS)")
    templates = canon / "_Движок" / "Шаблоны"
    for name in (JOURNAL_TEMPLATE, INCIDENTS_TEMPLATE):
        if not (templates / name).is_file():
            raise ConnectError(f"в каноне нет {templates / name} — канон неполный, подключение остановлено")
    if not project.is_dir():
        raise ConnectError(f"папки проекта нет: {project}")
    passport = project / "CLAUDE.md"
    text = passport.read_text(encoding="utf-8") if passport.is_file() else ""
    stamp = verified.isoformat() if verified else (current_verified(text) or today.isoformat())
    journal_path = shown(project, journal, True) if journal else block_value(text, JOURNAL_LINE)
    incidents_path = shown(project, incidents, False) if incidents else block_value(text, INCIDENTS_LINE)
    defaults = []
    if not journal_path:
        journal_path = DEFAULT_JOURNAL
        defaults.append(journal_path)
    if not incidents_path:
        incidents_path = DEFAULT_INCIDENTS
        defaults.append(incidents_path)
    missing_defaults = [d for d in defaults if not resolve(project, d).exists()]
    if missing_defaults:
        similar = candidates(project)
        if similar:
            listed = "; ".join(str(p.relative_to(project)) for p in similar[:5])
            raise ConnectError(f"в проекте уже есть похожие файлы ({listed}) — укажите свои ключами --journal и "
                               f"--incidents, чтобы не завести второй экземпляр; пути по умолчанию — теми же ключами")
    journal_state = ensure_journal(project, journal_path, templates / JOURNAL_TEMPLATE, canon, today)
    incidents_state = ensure_incidents(project, incidents_path, templates / INCIDENTS_TEMPLATE, canon, today)
    link, git_exclude = "не нужна", "не нужно"
    if rules_import:
        link = ensure_link(project, connection)
        git_exclude = exclude_from_git(project)
    new_block = block(canon, stamp, rules_import, journal_path, incidents_path)
    write_block(passport, new_block)
    return Report(passport, new_block.count("\n") + 1, link, git_exclude, stamp, journal_state, incidents_state)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Подключить проект к канону Knowledge-OS")
    parser.add_argument("--project", required=True, help="корень проекта — папка, где открывают чаты")
    parser.add_argument("--canon", default=None, help="корень канона (по умолчанию KNOWLEDGE_OS или D:/Knowledge-OS)")
    parser.add_argument("--verified", default=None, help="дата сверки с каноном ГГГГ-ММ-ДД (обновить строку)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-rules-import", action="store_true",
                      help="не импортировать правила агента в проект")
    mode.add_argument("--rules-import", action="store_true",
                      help="импортировать правила в проект, даже если глобальный CLAUDE.md их уже грузит")
    parser.add_argument("--journal", default=None,
                        help="свой журнал действий проекта: папка помесячных файлов или файл (от корня проекта)")
    parser.add_argument("--incidents", default=None, help="своя база инцидентов проекта (файл, от корня проекта)")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    try:
        report = connect(Path(args.project), Path(args.canon) if args.canon else default_canon(),
                         verified=date.fromisoformat(args.verified) if args.verified else None,
                         rules_import=False if args.no_rules_import else (True if args.rules_import else None),
                         journal=args.journal, incidents=args.incidents)
    except (ConnectError, ValueError, OSError) as exc:
        print(f"❌ {exc}")
        return 1
    print(f"паспорт: {report.passport}; блок «Канон Knowledge-OS»: строк {report.block_lines}")
    print(f"ссылка .kos: {report.link}; исключение из git: {report.git_exclude}; канон сверен: {report.verified}")
    print(f"журнал действий: {report.journal}; база инцидентов: {report.incidents}")
    print("ИТОГ: проект подключён — новые чаты в этой папке получат правила агента и пути канона")
    return 0


if __name__ == "__main__":
    sys.exit(main())
