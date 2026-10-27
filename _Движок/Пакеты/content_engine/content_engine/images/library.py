"""Провайдер `library` (Д7): готовые фото проекта — модели, продукт в интерьере, монтаж, — 0 ₽.

Индекс — CSV `images.library.index` (UTF-8, «;»): `file_or_url;alt;keywords`. Фото выбирается по совпадению основ
ключевых слов с заголовком и первым абзацем раздела: нужна хотя бы одна общая основа, побеждает наибольшее
совпадение; одно фото в статье — не больше одного раза. Фото уже лежит в хранилище сайта (адрес) — загрузка не
нужна; лежит файлом — копируется в папку картинок темы и загружается как остальные.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

from ..core.errors import EngineError


class LibraryProvider:
    name = "library"

    def __init__(self, instance):
        self.instance = instance
        path = Path(instance.p["images"]["library"]["index"])
        self.path = path if path.is_absolute() else instance.root / path
        if not self.path.is_file():
            raise EngineError(f"индекса готовых картинок нет: {self.path} (images.library.index)")
        with open(self.path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        if rows and [c.strip().lower() for c in rows[0][:3]] == ["file_or_url", "alt", "keywords"]:
            rows = rows[1:]
        self.rows = [{"ref": r[0].strip(), "alt": r[1].strip(), "keywords": r[2].strip()} for r in rows
                     if len(r) >= 3 and r[0].strip()]
        norm = instance.norm
        self._keys = [set().union(*norm.key(r["keywords"]).parts) if norm.key(r["keywords"]).parts else set()
                      for r in self.rows]

    def match(self, text: str, used: set[str]) -> dict | None:
        key = self.instance.norm.key(text)
        words = set().union(*key.parts) if key.parts else set()
        best = None
        for row, keys in zip(self.rows, self._keys):
            if row["ref"] in used:
                continue
            score = len(keys & words)
            if score and (best is None or score > best[0]):
                best = (score, row)
        return best[1] if best else None

    def price(self, request) -> float:
        return 0.0

    def make(self, request, path: Path) -> dict:
        ref = request.extra.get("ref", "")
        if ref.startswith("http"):
            return {"file": "", "url": ref, "price": 0.0}
        source = Path(ref)
        source = source if source.is_absolute() else self.path.parent / source
        if not source.is_file():
            raise EngineError(f"файла из индекса нет: {source}")
        target = Path(path).with_suffix(source.suffix)
        shutil.copyfile(source, target)
        return {"file": str(target), "url": "", "price": 0.0}
