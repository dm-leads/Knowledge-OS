"""Снимки каталога операций системы с кодом и их сверка (шаг 4 Контент-машины, 27.10.2026).

Пакет с командой `catalog` (контракт операций в коде, `<пакет> catalog --json | --md`) печатает, что делает каждая
его команда. Каталог генерируется из кода выпуска и руками не правится; в каноне лежат его снимки ПО ВЕРСИЯМ —
`<папка снимков>/<версия>.json` и `.md`, — чтобы методология, навык и будущий сервер MCP ссылались на смысл
конкретного выпуска. Сверяется пара «выпуск ↔ его снимок», а не старый снимок против свежего кода канона:
- `write` — снять каталог пакета канона в снимки его версии; снимок уже вышедшей версии не переписывается (другой
  каталог — новая версия пакета); печатается разница с предыдущей версией;
- `check` — каталог копии кода (пакет канона или выпуск в проекте) совпадает со снимком своей версии; у выпуска ещё
  и хэш каталога в паспорте (`catalog_sha256`, пишет `kos_release.py issue`).

Каталог снимается запуском той самой копии кода (`python -P -B`, путь — к её родительской папке), а не установленного
пакета; кэш Python в чужой папке не остаётся. Хэш — sha256 текста `--json` с LF.

Запуск:
  py -3 kos_catalog_check.py write --package <пакет в каноне> --snapshots <папка снимков>
  py -3 kos_catalog_check.py check --code <каталог кода: в каноне или выпуск> --snapshots <папка снимков>
Код возврата 1 — снимка нет, он расходится с кодом или с паспортом выпуска; печатается причина.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from kos_release import PASSPORT, ReleaseError, code_folder, package_version, read_passport


def run_catalog(code: Path, fmt: str) -> str:
    """Вывод `catalog --json|--md` именно этой копии кода; текст с LF."""
    code = Path(code).resolve()
    script = (f"import sys; sys.path.insert(0, {str(code.parent)!r}); from {code.name}.cli import main; "
              f"sys.exit(main(['catalog', '--{fmt}']))")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, "-P", "-B", "-c", script], capture_output=True, text=True, encoding="utf-8",
                          env=env)
    if done.returncode != 0:
        raise ReleaseError(f"`{code.name} catalog --{fmt}` в {code} не отработал: {done.stderr.strip()[-400:]}")
    return done.stdout.replace("\r\n", "\n")


def catalog_hash(code: Path) -> str:
    return hashlib.sha256(run_catalog(code, "json").encode("utf-8")).hexdigest()


def _version(code: Path) -> str:
    passport = read_passport(code)
    if passport is not None:
        return passport["version"]
    if not (Path(code) / "cli.py").is_file():
        raise ReleaseError(f"{code} — не каталог кода (нет cli.py): укажите папку кода пакета, а не папку с pyproject.toml")
    return package_version(Path(code).resolve().parent)[1]


def _ops(text: str) -> dict:
    return {op["command"]: op for op in json.loads(text)["operations"]}


def _previous(snapshots: Path, version: str) -> Path | None:
    def key(path):
        return tuple(int(part) for part in path.stem.split("."))
    older = [p for p in snapshots.glob("*.json") if p.stem.replace(".", "").isdigit() and key(p) < key(Path(version))]
    return max(older, key=key) if older else None


def diff(old: str, new: str) -> list[str]:
    """Разница каталогов по операциям: добавлены, убраны, изменены (контракт или аргументы)."""
    a, b = _ops(old), _ops(new)
    lines = [f"+ {c}" for c in sorted(set(b) - set(a))] + [f"- {c}" for c in sorted(set(a) - set(b))]
    lines += [f"~ {c}" for c in sorted(set(a) & set(b)) if a[c] != b[c]]
    return lines


def write(package: Path, snapshots: Path) -> list[str]:
    """Снять каталог пакета канона в снимки его версии; вернуть строки отчёта."""
    package, snapshots = Path(package), Path(snapshots)
    version = package_version(package)[1]
    code = code_folder(package)
    texts = {"json": run_catalog(code, "json"), "md": run_catalog(code, "md")}
    report = []
    for fmt, text in texts.items():
        target = snapshots / f"{version}.{fmt}"
        if target.is_file():
            if target.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
                raise ReleaseError(f"снимок {target.name} версии {version} уже есть и с кодом не совпадает — каталог "
                                   "вышедшей версии не переписывается: поднимите версию пакета")
            report.append(f"{target.name}: совпадает, не переписан")
            continue
        snapshots.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        report.append(f"{target.name}: записан")
    previous = _previous(snapshots, version)
    if previous is not None:
        changes = diff(previous.read_text(encoding="utf-8"), texts["json"])
        report.append(f"разница с {previous.stem}: " + (", ".join(changes) if changes else "нет"))
    return report


def check(code: Path, snapshots: Path) -> list[str]:
    """Расхождения копии кода со снимком своей версии и с хэшем в паспорте выпуска; пусто — всё сходится."""
    code, snapshots = Path(code), Path(snapshots)
    version = _version(code)
    problems = []
    texts = {}
    for fmt in ("json", "md"):
        snap = snapshots / f"{version}.{fmt}"
        texts[fmt] = run_catalog(code, fmt)
        if not snap.is_file():
            problems.append(f"нет снимка {snap.name} версии {version} в {snapshots}")
        elif snap.read_text(encoding="utf-8").replace("\r\n", "\n") != texts[fmt]:
            problems.append(f"{snap.name} не совпадает с каталогом кода "
                            + (f"({', '.join(diff(snap.read_text(encoding='utf-8'), texts['json']))})"
                               if fmt == "json" else ""))
    passport = read_passport(code)
    if passport is not None:
        actual = hashlib.sha256(texts["json"].encode("utf-8")).hexdigest()
        if passport.get("catalog_sha256") is None:
            problems.append(f"в паспорте выпуска {PASSPORT} нет catalog_sha256 — выпуск сделан старым kos_release")
        elif passport["catalog_sha256"] != actual:
            problems.append("хэш каталога не совпадает с паспортом выпуска — выпуск правили руками")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Снимки каталога операций по версиям и их сверка с кодом")
    commands = parser.add_subparsers(dest="command", required=True)
    w = commands.add_parser("write", help="снять каталог пакета канона в снимки его версии")
    w.add_argument("--package", required=True, help="пакет в каноне: папка с pyproject.toml")
    w.add_argument("--snapshots", required=True, help="папка снимков каталога")
    c = commands.add_parser("check", help="сверить копию кода со снимком своей версии")
    c.add_argument("--code", required=True, help="каталог кода: в каноне или выпуск в проекте")
    c.add_argument("--snapshots", required=True, help="папка снимков каталога")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    try:
        if args.command == "write":
            print("\n".join(write(Path(args.package), Path(args.snapshots))))
            return 0
        problems = check(Path(args.code), Path(args.snapshots))
    except ReleaseError as exc:
        print(f"❌ {exc}")
        return 1
    for line in problems:
        print(f"❌ {line}")
    if not problems:
        print(f"✅ каталог {Path(args.code).name} {_version(Path(args.code))} совпадает со снимком")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
