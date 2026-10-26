"""Контракт сборщика (Т20.2–Т20.3, Т20.5): `Assembler(instance, out_dir).build(статья, разметка, картинки, бриф) → Package`.

Пакет — папка с файлами и `manifest.json`: файлы с SHA-256, title, description, slug, H1, canonical, день сборки,
главный файл и порядок блоков для вставки, текст статьи без разметки (`text.txt` — эталон «доехало»). Сборщик проекта
из инстанса (`module:<путь>:<класс>`) получает тот же вход и обязан выдать тот же манифест — это проверяет
`assemble contract`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..core.errors import EngineError
from ..core.store import SCHEMA_VERSION

MANIFEST = "manifest.json"
REQUIRED = ("id", "built", "title", "description", "slug", "h1", "canonical", "main", "blocks", "files")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class Package:
    folder: Path
    manifest: dict

    @property
    def main(self) -> Path:
        return self.folder / self.manifest["main"]

    def text(self) -> str:
        path = self.folder / "text.txt"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    @classmethod
    def load(cls, folder) -> "Package":
        """Прочитать пакет и сверить суммы всех файлов манифеста."""
        folder = Path(folder)
        path = folder / MANIFEST
        if not path.is_file():
            raise EngineError(f"пакета нет: {path} — сначала `assemble build`")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        problems = check_manifest(folder, manifest)
        if problems:
            raise EngineError(f"пакет {folder.name}: " + "; ".join(problems))
        return cls(folder, manifest)


def check_manifest(folder: Path, manifest: dict) -> list[str]:
    problems = [f"в манифесте нет поля «{name}»" for name in REQUIRED if name not in manifest]
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    for name, digest in files.items():
        path = Path(folder) / name
        if not path.is_file():
            problems.append(f"файла {name} нет")
        elif sha256(path) != digest:
            problems.append(f"сумма {name} не совпадает с манифестом")
    for name in [manifest.get("main"), *(manifest.get("blocks") or [])]:
        if name and name not in files:
            problems.append(f"файл {name} не описан в манифесте")
    extra = sorted(p.name for p in Path(folder).iterdir() if p.is_file() and p.name != MANIFEST
                   and p.name not in files)
    if extra:
        problems.append(f"файлы вне манифеста: {extra}")
    return problems


def write_manifest(folder: Path, meta: dict, files: list[str]) -> Package:
    """Манифест с суммами всех файлов пакета; читается обратно и сверяется."""
    folder = Path(folder)
    manifest = {"schema_version": SCHEMA_VERSION, "kind": "package_manifest", **meta,
                "files": {name: sha256(folder / name) for name in files}}
    (folder / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8",
                                   newline="\n")
    return Package.load(folder)


class Assembler(Protocol):
    name: str

    def __init__(self, instance, out_dir: Path): ...

    def build(self, article_path: Path, schema: dict, images: list[dict], brief: dict) -> Package:
        """Собрать пакет в `out_dir`: файлы страницы и манифест."""


def load_assembler(instance):
    from ..core.plugins import load_class
    from .html import HtmlAssembler
    from .markdown import MarkdownAssembler
    return load_class(instance, instance.p["assemble"]["adapter"],
                      {"html": HtmlAssembler, "markdown": MarkdownAssembler}, "assemble.adapter")
