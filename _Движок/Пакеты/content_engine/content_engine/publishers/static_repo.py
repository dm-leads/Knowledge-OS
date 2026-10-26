"""Публикатор статического сайта (Т21.3): файл статьи в репозиторий сайта, коммит, push по правилам репозитория.

Ступени (Т16.1): `commit` — файл в `publish.repo_path` и коммит без публикации; `publish` — push, после которого сайт
пересобирается своим конвейером. Подтверждение: коммит — чтением файла из HEAD (`git show`), push — ответом живой
страницы (код 200 и заголовок статьи). Снимок «до»: файл в репозитории (был ли, что в нём) и живая страница по адресу.
Откат — тем же путём: вернуть прежний файл (или убрать новый), коммит, push.

Коммит берёт только путь статьи (`git commit -- <путь>`): чужие подготовленные правки остаются в индексе. Перед push
проверяется, что неотправленные коммиты ветки — только коммиты машины этого прогона, а хост удалённого репозитория —
из `publish.hosts` (Т16.2); иначе push не идёт.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.actions import Action
from ..core.errors import EngineError, SourceError


def run_git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise SourceError(f"git {' '.join(args[:2])}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def repo_root(folder: Path) -> Path:
    for path in [folder, *folder.parents]:
        if (path / ".git").exists():
            return path
    raise EngineError(f"publish.repo_path: {folder} не внутри репозитория git")


class StaticRepoPublisher:
    name = "static_repo"

    def __init__(self, instance, *, transport=None, git=run_git):
        from ..adapters.page_fetch import PageFetcher
        self.instance = instance
        self.git = git
        folder = Path(instance.p["publish"]["repo_path"])
        if not str(folder):
            raise EngineError("publish.repo_path пуст: static_repo не знает, куда класть статью")
        self.folder = folder if folder.is_absolute() else instance.root / folder
        self.root = repo_root(self.folder)
        self.fetcher = PageFetcher.for_own_site(instance, transport=transport)
        self.hosts = {h.lower() for h in instance.p["publish"]["hosts"]}
        self.commits: list[str] = []          # коммиты машины этого прогона — только их можно отправить

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def read_back(self, url: str) -> tuple[int, str]:
        page = self.fetcher.get(url, allow_redirects=False)
        return page.status, page.text

    def _file_state(self, path: Path) -> dict:
        exists = path.is_file()
        return {"exists": exists, "content": path.read_text(encoding="utf-8") if exists else None,
                "read_from": str(path)}

    def _live_state(self, url: str) -> dict:
        status, text = self.read_back(url)
        if status not in (200, 404, 410):
            raise SourceError(f"снимок «до» {url}: HTTP {status} — состояние страницы неизвестно, записи нет")
        return {"exists": status == 200, "content": text if status == 200 else None, "read_from": url,
                "meta": {"status": status}}

    def _shown(self, path: Path) -> tuple[bool, str]:
        relpath = self._rel(path)
        shown = subprocess.run(["git", "-C", str(self.root), "show", f"HEAD:{relpath}"], capture_output=True,
                               text=True, encoding="utf-8")
        return shown.returncode == 0, shown.stdout

    def _commit(self, path: Path, content: str | None, message: str) -> Action:
        rel = self._rel(path)

        def do():
            if content is None:
                if path.is_file():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
            self.git(self.root, "add", "-A", "--", rel)
            self.git(self.root, "commit", "-m", message, "--", rel)      # только свой путь
            head = self.git(self.root, "rev-parse", "HEAD").strip()
            self.commits.append(head)
            return head

        def verify(head):
            found, text = self._shown(path)
            if content is None:
                return not found, f"коммит {head[:7]}: файла {path.name} в HEAD " + ("нет" if not found else "всё ещё есть")
            ok = found and text == content
            return ok, f"коммит {head[:7]}: файл {path.name} в HEAD " + ("совпал" if ok else "не совпал")

        return Action("commit", rel, "git commit", message, "read_back", ("publish", "commit"), do=do,
                      verify=verify, snapshot=lambda: self._file_state(path))

    def _push(self, url: str, expect: str | None, what: str) -> Action:
        def do():
            self._push_guard()
            self.git(self.root, "push", "origin", f"HEAD:refs/heads/{self._branch()}")   # явная ветка, не matching
            return None

        def verify(_):
            status, text = self.read_back(url)
            if expect is None:
                return status != 200, f"{url}: HTTP {status}"
            ok = status == 200 and expect in text
            return ok, f"{url}: HTTP {status}" + ("" if ok else f", «{expect[:40]}» на странице не найден")

        return Action("push", url, "git push", what, "read_back", ("publish", "publish"), do=do, verify=verify,
                      snapshot=lambda: self._live_state(url))

    def _push_guard(self):
        from urllib.parse import urlsplit
        remote = self.git(self.root, "remote", "get-url", "origin").strip()
        host = (urlsplit(remote).hostname or "") if "://" in remote else \
            (remote.split("@", 1)[-1].split(":", 1)[0] if "@" in remote else "")
        if not host and not Path(remote).exists():
            host = remote.split(":", 1)[0] if ":" in remote else remote      # «host:path» без «user@»
        target = host.lower() if host else str(Path(remote).resolve()).lower()   # локальный путь — тоже по списку
        allowed = self.hosts | {str(Path(h).resolve()).lower() for h in self.hosts if Path(h).exists()}
        if target not in allowed:
            raise SourceError(f"push: удалённый репозиторий «{host or remote}» не из publish.hosts — не отправлено")
        pending = self.git(self.root, "rev-list", f"origin/{self._branch()}..HEAD").split()
        foreign = [c for c in pending if c not in self.commits]
        if foreign:
            raise SourceError(f"push: в ветке неотправленные коммиты не этого прогона ({len(foreign)}) — "
                              "не отправлено, разберите репозиторий сайта")

    def _branch(self) -> str:
        branch = self.git(self.root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if not branch or branch == "HEAD":
            raise SourceError("push: репозиторий сайта не на ветке — не отправлено")
        return branch

    def plan(self, package) -> list[Action]:
        import re
        m = package.manifest
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(m.get("slug", ""))):
            raise EngineError(f"static_repo: slug «{m.get('slug')}» — латиница, цифры, одиночные дефисы")
        path = self.folder / f"{m['slug']}{package.main.suffix}"
        if self.folder.resolve() not in path.resolve().parents:
            raise EngineError(f"static_repo: путь {path} вне publish.repo_path — записи нет")
        content = package.main.read_text(encoding="utf-8")
        return [self._commit(path, content, f"content-engine: статья {m['slug']}"),
                self._push(m["canonical"], m["h1"], f"публикация статьи {m['slug']}")]

    def restore(self, snapshots: list[dict]) -> list[Action]:
        out = []
        for snap in snapshots:
            if snap["action"] == "commit":
                out.append(self._commit(Path(snap["read_from"]), snap["content"] if snap["exists"] else None,
                                        f"content-engine: откат {snap['run_id']}"))
            elif snap["action"] == "push":
                content = snap["content"]
                marker = None if not snap["exists"] else content
                action = self._push(snap["target"], None, f"откат публикации {snap['run_id']}")
                if marker is not None:
                    def verify(_, url=snap["target"], before=content):
                        status, text = self.read_back(url)
                        ok = status == 200 and text == before
                        return ok, f"{url}: HTTP {status}, страница " + ("как в снимке «до»" if ok else "другая")
                    action.verify = verify
                out.append(action)
        return out
