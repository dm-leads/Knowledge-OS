"""Передача статьи в папку проекта (Н1): после «доехало» текст и разметка ложатся туда, откуда корпус проекта узнаёт
статью, — в один шаг, а не руками.

Папка лежит вне инстанса, поэтому это запись вовне со своей ступенью `paths.allow` (`files` — только план, `write` —
передача; выше `files` — по разрешению владельца строкой профиля и журнала действий). Без `--apply` — план. Снимок «до»
— есть ли папка и суммы её файлов; папка уже есть с теми же файлами — «уже есть», ноль записей; с другими или чужими
файлами — отказ без перезаписи. Подтверждение — суммы прочитаны обратно и корпус находит статью по slug. Откат —
`publish rollback` по виду снимка (`core/restore.py`): удаляются только файлы, созданные машиной.

Раскладка по умолчанию (`paths.transfer: builtin`) — папка по шаблону `paths.published_dir` с подстановками `{slug}`,
`{h1}`, `{id}`; у проекта со своей архитектурой — адаптер `module:<путь>:<класс>` по тому же контракту
(`plan(tid, url) → действия`, `exists(tid, url) → bool`).
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from .actions import Action
from .errors import EngineError, GateRefused

KIND = "article_transfer"
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean_name(text: str) -> str:
    """Имя папки, допустимое в Windows: без запрещённых знаков, пробелы сжаты, без точек и пробелов по краям."""
    name = re.sub(r"\s+", " ", BAD.sub("", text or "")).strip(" .")
    return name[:120].rstrip(" .")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_root(start: Path) -> Path:
    for folder in [start, *start.parents]:
        if (folder / ".git").exists():
            return folder
    return start.parent


class BuiltinTransfer:
    name = "builtin"

    def __init__(self, instance):
        self.instance = instance
        self.p = instance.p["paths"]

    def _manifest(self, tid: str) -> dict:
        from ..assemblers.base import Package
        return Package.load(self.instance.brief_dir(tid) / "package").manifest

    def folder(self, tid: str) -> Path:
        m = self._manifest(tid)
        subs = {"slug": m["slug"], "h1": m["h1"], "id": tid}
        parts = []
        for part in self.p["published_dir"].replace("\\", "/").split("/"):
            for key, value in subs.items():
                part = part.replace("{" + key + "}", clean_name(value))
            parts.append(part)
        target = (self.instance.root / "/".join(parts)).resolve()
        root = repo_root(self.instance.root).resolve()
        if root != target and root not in target.parents:
            raise EngineError(f"paths.published_dir: папка {target} вне репозитория проекта {root}")
        return target

    def sources(self, tid: str) -> dict[str, Path]:
        folder = self.instance.brief_dir(tid)
        names = self.p["published_files"]
        out = {names["article"]: folder / "article.md", names["schema"]: folder / "schema.json"}
        missing = [str(p) for p in out.values() if not p.is_file()]
        if missing:
            raise GateRefused(f"тема {tid}: нечего передавать — нет {missing}")
        return out

    def exists(self, tid: str, url: str) -> bool:
        folder = self.folder(tid)
        return all((folder / name).is_file() and _sha(folder / name) == _sha(src)
                   for name, src in self.sources(tid).items())

    def plan(self, tid: str, url: str) -> list[Action]:
        folder, sources = self.folder(tid), self.sources(tid)
        if folder.is_dir():
            ours = set(sources)
            foreign = sorted(p.name for p in folder.iterdir() if p.name not in ours)
            differ = sorted(name for name, src in sources.items()
                            if (folder / name).is_file() and _sha(folder / name) != _sha(src))
            if foreign or differ:
                raise GateRefused(f"папка {folder} уже есть: " + "; ".join(
                    ([f"чужие файлы {foreign}"] if foreign else []) + ([f"другие {differ}"] if differ else []))
                    + " — машина не перезаписывает, решает человек")
        slug = self._manifest(tid)["slug"]

        def snapshot():
            return {"exists": folder.is_dir(), "read_from": str(folder),
                    "content": {name: (_sha(folder / name) if (folder / name).is_file() else None) for name in sources},
                    "meta": {"folder_existed": folder.is_dir(), "folder": str(folder)}}

        def do():
            folder.mkdir(parents=True, exist_ok=True)
            for name, src in sources.items():
                shutil.copyfile(src, folder / name)
            return folder

        def verify(_):
            bad = [name for name, src in sources.items()
                   if not (folder / name).is_file() or _sha(folder / name) != _sha(src)]
            if bad:
                return False, f"после копии суммы не сходятся: {bad}"
            return found_in_corpus(self.instance, folder, slug)

        return [Action(KIND, str(folder), "copy", f"текст и разметка статьи {tid} → папка проекта", "read_back",
                       ("paths", "write"), do=do, verify=verify, snapshot=snapshot)]


def found_in_corpus(instance, folder: Path, slug: str) -> tuple[bool, str]:
    """Корпус проекта, прочитанный заново, видит статью; папка вне `corpus.folders` — не сверяется, так и пишется."""
    from ..adapters.corpus import markdown_folder
    covered = False
    for spec in instance.p["corpus"]["folders"]:
        base = Path(spec["path"])
        base = (base if base.is_absolute() else instance.root / base).resolve()
        if base == folder or base in folder.parents:
            covered = True
    if not covered:
        return True, f"файлы на месте в {folder}; корпус не сверялся — папка вне corpus.folders"
    pages, _ = markdown_folder(instance.p, instance.root)
    if any(p.slug == slug for p in pages):
        return True, f"файлы на месте в {folder}; корпус видит статью «{slug}»"
    return False, f"файлы на месте в {folder}, но корпус не нашёл статью «{slug}» — проверьте corpus.folders"


def load(instance):
    from .plugins import load_class
    return load_class(instance, instance.p["paths"]["transfer"], {"builtin": BuiltinTransfer}, "paths.transfer")(instance)
