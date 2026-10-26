"""Попадает ли файл инстанса под правила игнорирования git (Т42.4 частично, урок волны 1) — без вызова git.

У потребителя shell `git` запрещён, поэтому правила читаются из файлов `.gitignore` от корня репозитория (папка с
`.git`) до папки файла и сопоставляются по правилам gitignore: шаблоны `*`, `?`, `[…]`, `**`, привязка к папке
`.gitignore` при «/» внутри шаблона, только каталоги при «/» в конце, исключения `!`; последнее совпадение главнее;
файл внутри игнорируемой папки исключением не возвращается. `.git/info/exclude` и глобальный `core.excludesFile`
не сверяются — так и пишет пункт `check`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Rule:
    base: Path               # папка файла .gitignore
    regex: re.Pattern
    negate: bool
    dir_only: bool
    text: str
    source: Path


def repo_root(start: Path) -> Path | None:
    for path in [start, *start.parents]:
        if (path / ".git").exists():
            return path
    return None


def _translate(pattern: str) -> str:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out += "(?:/.*)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out += re.escape(pattern[i])
                i += 1
            else:
                body = pattern[i + 1:end].replace("\\", "\\\\")
                out += "[" + ("^" + body[1:] if body.startswith("!") else body) + "]"
                i = end + 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return out


def parse(path: Path) -> list[Rule]:
    rules = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        if line.startswith("\\"):
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = "/" in line
        line = line.lstrip("/")
        body = _translate(line)
        regex = re.compile(("^" if anchored else "^(?:.*/)?") + body + "$")
        rules.append(Rule(path.parent, regex, negate, dir_only, raw.strip(), path))
    return rules


class Ignore:
    def __init__(self, start: Path):
        self.root = repo_root(Path(start).resolve())
        self._cache: dict[Path, list[Rule]] = {}

    def _rules_for(self, folder: Path) -> list[Rule]:
        """Правила всех .gitignore от корня репозитория до папки — по порядку."""
        chain = []
        for path in reversed([folder, *folder.parents]):
            if self.root not in (path, *path.parents):
                continue
            if path not in self._cache:
                file = path / ".gitignore"
                self._cache[path] = parse(file) if file.is_file() else []
            chain += self._cache[path]
        return chain

    def _match(self, path: Path, is_dir: bool) -> Rule | None:
        found = None
        for rule in self._rules_for(path.parent):
            if rule.dir_only and not is_dir:
                continue
            try:
                rel = path.relative_to(rule.base).as_posix()
            except ValueError:
                continue
            if rule.regex.match(rel):
                found = rule
        return found

    def ignored(self, path) -> tuple[bool, str]:
        """(игнорируется ли, каким правилом и в каком файле)."""
        if self.root is None:
            return False, "не в репозитории git"
        path = Path(path).resolve()
        rel_parts = path.relative_to(self.root).parts
        current = self.root
        for part in rel_parts[:-1]:                    # игнорируемая папка не возвращается исключением
            current = current / part
            rule = self._match(current, True)
            if rule and not rule.negate:
                return True, f"папка {current.name}/: правило «{rule.text}» в {rule.source}"
        rule = self._match(path, path.is_dir())
        if rule and not rule.negate:
            return True, f"правило «{rule.text}» в {rule.source}"
        return False, ""
