"""Синтетический статический сайт для тестов публикации: репозиторий сайта, «удалённый» голый репозиторий и подмена
HTTP, которая отдаёт страницу так, как её отдал бы сайт после пересборки из отправленной ветки. Сети нет.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from content_engine.assemblers.base import write_manifest
from content_engine.core.urls import norm_url

DOMAIN = "example.ru"


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return out.stdout


class Page:
    def __init__(self, status: int, text: str = "", location: str = ""):
        self.status_code = status
        self.content = text.encode("utf-8")
        self.text = text
        self.encoding = "utf-8"
        self.headers = {"Location": location} if location else {}

    def json(self):
        import json
        return json.loads(self.text)


class StaticSite:
    """Сайт отдаёт blog/<slug>.html из ветки main голого репозитория; `extra` — подменённые адреса."""

    def __init__(self, root: Path):
        self.remote = root / "remote.git"
        self.work = root / "site"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)], capture_output=True, check=True)
        subprocess.run(["git", "init", "-b", "main", str(self.work)], capture_output=True, check=True)
        for key, value in (("user.name", "test"), ("user.email", "test@example.ru"), ("commit.gpgsign", "false")):
            git(self.work, "config", key, value)
        git(self.work, "remote", "add", "origin", str(self.remote))
        (self.work / "blog").mkdir()
        (self.work / "blog" / "index.html").write_text("<html><body>блог</body></html>", encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-m", "start")
        git(self.work, "push", "-u", "origin", "main")
        self.extra: dict[str, Page] = {}
        self.log: list[tuple[str, str]] = []

    def live(self, slug: str) -> str | None:
        out = subprocess.run(["git", "-C", str(self.remote), "show", f"main:blog/{slug}.html"], capture_output=True,
                             text=True, encoding="utf-8")
        return out.stdout if out.returncode == 0 else None

    def __call__(self, method, url, **kwargs):
        self.log.append((method, url))
        normal = norm_url(url)
        if normal in self.extra:
            return self.extra[normal]
        if normal == f"{DOMAIN}" or normal == f"{DOMAIN}/blog":
            return Page(200, "<html><body>главная</body></html>")
        parts = normal.split("/")
        if len(parts) == 3 and parts[1] == "blog":
            text = self.live(parts[2])
            if text is not None:
                return Page(200, text)
        return Page(404, "нет такой страницы")


def article_html(slug: str, h1: str, body: str, *, author="Автор Примеров", day="2026-03-02") -> str:
    return (f"<html><head><title>{h1}</title><meta name=\"description\" content=\"Описание статьи {slug}\">"
            f"<link rel=\"canonical\" href=\"https://{DOMAIN}/blog/{slug}\"></head><body><article><h1>{h1}</h1>"
            f"<p>{body}</p><p>Автор: {author}. Опубликовано {day}.</p></article></body></html>")


def make_package(folder: Path, tid: str, slug: str, h1: str, body: str, *, day="2026-03-02", read_also=()):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "article.html").write_text(article_html(slug, h1, body, day=day), encoding="utf-8", newline="\n")
    (folder / "text.txt").write_text(f"{h1}\n{body}\n", encoding="utf-8", newline="\n")
    meta = {"id": tid, "built": day, "title": h1, "description": f"Описание статьи {slug}", "slug": slug, "h1": h1,
            "canonical": f"https://{DOMAIN}/blog/{slug}", "main": "article.html", "blocks": ["article.html"],
            "read_also": list(read_also), "assembler": "test"}
    # как у настоящей сборки: пакет помнит суммы брифа и текста, из которых собран
    from content_engine.core.approval import digest
    article = folder.parent / "article.md"
    if not article.is_file():
        article.write_text(f"# {h1}\n\n{body}\n", encoding="utf-8", newline="\n")
    meta["article_sha256"] = digest(article)
    if (folder.parent / "brief.yaml").is_file():
        meta["brief_sha256"] = digest(folder.parent / "brief.yaml")
    return write_manifest(folder, meta, ["article.html", "text.txt"])


def brief_yaml(folder: Path, *, read_also=()):
    folder.mkdir(parents=True, exist_ok=True)
    data = {"h1": "Посадка чеснока", "title": "Посадка чеснока", "slug": "posadka-chesnoka",
            "author": {"name": "Автор Примеров"}, "read_also": list(read_also)}
    (folder / "brief.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8", newline="\n")
