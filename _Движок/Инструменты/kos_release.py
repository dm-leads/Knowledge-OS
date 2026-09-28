"""Выпуск системы с кодом из канона Knowledge-OS в проект и проверка выпуска (ADR-0003, 28.09.2026).

Исходник системы (движок роста, Ядро данных, следующие) живёт в каноне: `_Движок/Пакеты/<пакет>/` с `pyproject.toml`
и каталогом кода `<пакет>/`. Проект, которому код нужен в своём репозитории (компания хранит его в своём GitLab и
ставит на свой сервер), получает ВЫПУСК — копию каталога кода одной закоммиченной версии канона — и паспорт выпуска
рядом с кодом: пакет, версия, коммит канона, отпечаток каждого файла. Другие проекты подключают пакет ссылкой
(`pip install -e`) и выпуск не держат.

Правила:
- выпуск делается только из закоммиченного канона — иначе паспорт указывал бы на коммит, где этого кода нет;
- выпуск не правится руками: проверка находит изменённые, пропавшие и лишние файлы; правка идёт в канон и приходит
  новым выпуском. Поверх ручных правок новый выпуск не ложится — их сначала переносят в канон или отменяют;
- отпечаток считается по тексту с переводом строки LF: рабочая копия Git на Windows (CRLF) и сервер (LF) — один
  выпуск; кэш Python (`__pycache__`, `*.pyc`) в выпуск не входит и не проверяется;
- отставание от канона — число коммитов канона, менявших пакет после коммита выпуска; это не ошибка, а сигнал
  обновить выпуск.

Запуск (из любой папки):
  py -3 kos_release.py issue --package <пакет в каноне> --into <каталог кода в проекте> [--first]
  py -3 kos_release.py check --into <каталог кода в проекте> [--package <пакет в каноне>]
Код возврата 1 — выпуск правили руками или выпустить нельзя; печатается причина.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PASSPORT = "_паспорт_выпуска.json"
SKIPPED_DIRS = {"__pycache__", ".pytest_cache"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}


class ReleaseError(Exception):
    """Выпуск невозможен или выпуск правили руками: причина — текстом для человека."""


@dataclass
class Report:
    """Итог проверки выпуска: расхождения с паспортом и отставание от канона (если канон под рукой)."""
    package: str
    version: str
    commit: str
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    behind: int | None = None
    canon_version: str | None = None

    @property
    def ok(self) -> bool:
        return not (self.changed or self.missing or self.extra)


def fingerprint(path: Path) -> str:
    """Отпечаток файла по тексту с LF: CRLF рабочей копии Windows не считается правкой."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def release_files(folder: Path) -> dict[str, Path]:
    """Файлы каталога кода, которые входят в выпуск: без кэша Python и без самого паспорта."""
    files = {}
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if not path.is_file() or path.name == PASSPORT or path.suffix in SKIPPED_SUFFIXES:
            continue
        if SKIPPED_DIRS.intersection(relative.parts):
            continue
        files[relative.as_posix()] = path
    return files


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args[:2])}: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def package_version(package: Path) -> tuple[str, str]:
    """Имя и версия пакета из pyproject.toml канона."""
    project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8")).get("project") or {}
    if not project.get("name") or not project.get("version"):
        raise ReleaseError(f"в {package / 'pyproject.toml'} нет имени или версии пакета")
    return project["name"], project["version"]


def code_folder(package: Path) -> Path:
    """Каталог кода пакета — одноимённый корню пакета (`growth_engine/growth_engine/`)."""
    folder = package / package.name
    if not (folder / "__init__.py").is_file():
        raise ReleaseError(f"в пакете {package} нет каталога кода {package.name}/ с __init__.py")
    return folder


def read_passport(into: Path) -> dict | None:
    path = into / PASSPORT
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def compare(into: Path, passport: dict) -> tuple[list[str], list[str], list[str]]:
    """Изменённые, пропавшие и лишние файлы выпуска относительно паспорта."""
    actual = release_files(into)
    listed = passport["files"]
    changed = [name for name in sorted(listed) if name in actual and fingerprint(actual[name]) != listed[name]]
    missing = [name for name in sorted(listed) if name not in actual]
    extra = [name for name in sorted(actual) if name not in listed]
    return changed, missing, extra


def check(into: Path, package: Path | None = None) -> Report:
    """Проверка выпуска по паспорту; с пакетом канона — ещё и отставание от канона."""
    into = Path(into)
    passport = read_passport(into)
    if passport is None:
        raise ReleaseError(f"в {into} нет паспорта выпуска {PASSPORT} — это не выпуск канона")
    changed, missing, extra = compare(into, passport)
    report = Report(passport["package"], passport["version"], passport["canon_commit"], changed, missing, extra)
    if package is not None:
        package = Path(package)
        report.behind = int(git(package, "rev-list", "--count", f"{report.commit}..HEAD", "--", "."))
        report.canon_version = package_version(package)[1]
    return report


def issue(package: Path, into: Path, first: bool = False) -> dict:
    """Выпуск пакета канона в каталог кода проекта; возвращает записанный паспорт."""
    package, into = Path(package), Path(into)
    name, version = package_version(package)
    source = code_folder(package)
    if git(package, "status", "--porcelain", "--", "."):
        raise ReleaseError(f"пакет {name} в каноне не закоммичен — выпуск делается только с коммита канона")
    commit = git(package, "log", "-1", "--format=%H", "--", ".")
    old = read_passport(into) if into.exists() else None
    if old is not None:
        changed, missing, extra = compare(into, old)
        if changed or missing or extra:
            raise ReleaseError(f"выпуск в {into} правили руками ({len(changed)} изменено, {len(missing)} пропало, "
                               f"{len(extra)} лишних) — перенесите правки в канон или отмените их")
    elif into.exists() and any(into.iterdir()) and not first:
        raise ReleaseError(f"в {into} уже лежит код без паспорта выпуска — первый выпуск поверх него только с "
                           "ключом --first")
    files = release_files(source)
    if into.exists():
        for old_file in release_files(into).values():
            old_file.unlink()
    for relative, path in files.items():
        target = into / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for folder in sorted((p for p in into.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    passport = {"package": name, "version": version, "canon_commit": commit, "released": date.today().isoformat(),
                "canon_path": package.as_posix(), "fingerprint": "sha256 текста с LF",
                "files": {relative: fingerprint(path) for relative, path in files.items()}}
    (into / PASSPORT).write_text(json.dumps(passport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                                 newline="\n")
    return passport


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Выпуск системы с кодом из канона в проект и проверка выпуска")
    commands = parser.add_subparsers(dest="command", required=True)
    issue_cmd = commands.add_parser("issue", help="выпустить пакет канона в каталог кода проекта")
    issue_cmd.add_argument("--package", required=True, help="пакет в каноне: папка с pyproject.toml")
    issue_cmd.add_argument("--into", required=True, help="каталог кода в проекте")
    issue_cmd.add_argument("--first", action="store_true", help="первый выпуск поверх кода без паспорта")
    check_cmd = commands.add_parser("check", help="проверить выпуск по паспорту и отставание от канона")
    check_cmd.add_argument("--into", required=True, help="каталог кода в проекте")
    check_cmd.add_argument("--package", help="пакет в каноне — чтобы посчитать отставание")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    try:
        if args.command == "issue":
            passport = issue(Path(args.package), Path(args.into), args.first)
            print(f"выпуск {passport['package']} {passport['version']} (коммит канона "
                  f"{passport['canon_commit'][:7]}) → {args.into}; файлов: {len(passport['files'])}")
            return 0
        report = check(Path(args.into), Path(args.package) if args.package else None)
    except ReleaseError as exc:
        print(f"❌ {exc}")
        return 1
    print(f"выпуск {report.package} {report.version} (коммит канона {report.commit[:7]})")
    for label, names in (("изменены", report.changed), ("пропали", report.missing), ("лишние", report.extra)):
        if names:
            print(f"❌ {label}: {', '.join(names)}")
    if report.behind:
        print(f"⚠️ канон ушёл вперёд: изменений пакета после выпуска — {report.behind}, версия в каноне "
              f"{report.canon_version}; обновить: issue --package … --into …")
    elif report.behind == 0:
        print("канон: выпуск свежий")
    print("ИТОГ: " + ("выпуск совпадает с паспортом" if report.ok else "выпуск правили руками — правку в канон"))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
