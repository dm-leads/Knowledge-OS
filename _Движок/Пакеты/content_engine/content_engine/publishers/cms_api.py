"""Публикатор через API CMS (Т21.3) по образцу REST WordPress (`/wp-json/wp/v2/posts`).

Два действия: черновик (`status=draft`, ступень `commit`) и публикация отдельным действием (`status=publish`, ступень
`publish`). Уже опубликованная запись с тем же slug обновляется только на ступени `publish`: правка живой страницы.
Подтверждение — чтением записи обратно. Снимок «до» — запись по slug (была ли, её заголовок, текст и статус). Откат —
вернуть заголовок, текст и статус из снимка; новая запись, которой не было, удаляется в корзину. Хосты — `publish.hosts`,
учётка — по именам переменных (`publish.cms.user_env`, `token_env`).
"""
from __future__ import annotations

import base64

from ..adapters.write_client import WriteClient
from ..core.actions import Action
from ..core.env_file import secret
from ..core.errors import EngineError

POSTS = "wp-json/wp/v2/posts"


class CmsApiPublisher:
    name = "cms_api"

    def __init__(self, instance, *, transport=None):
        from ..adapters.page_fetch import PageFetcher
        self.instance = instance
        cms = instance.p["publish"]["cms"]
        if not cms["base_url"]:
            raise EngineError("publish.cms.base_url пуст: cms_api не знает адреса CMS")
        headers = {}
        if cms["token_env"]:
            token = secret(instance.p, cms["token_env"])
            if cms["user_env"]:
                pair = f"{secret(instance.p, cms['user_env'])}:{token}".encode("utf-8")
                headers["Authorization"] = "Basic " + base64.b64encode(pair).decode("ascii")
            else:
                headers["Authorization"] = f"Bearer {token}"
        allowed = {("GET", POSTS), ("GET", f"{POSTS}/*"), ("POST", POSTS), ("POST", f"{POSTS}/*"),
                   ("DELETE", f"{POSTS}/*")}
        self.client = WriteClient("cms_api", cms["base_url"], allowed, headers, hosts=instance.p["publish"]["hosts"],
                                  transport=transport if transport is not None else instance.transport)
        self.fetcher = PageFetcher.for_own_site(instance, transport=transport)

    def read_back(self, url: str) -> tuple[int, str]:
        page = self.fetcher.get(url, allow_redirects=False)
        return page.status, page.text

    def find(self, slug: str) -> dict | None:
        answer = self.client.call("GET", POSTS, params={"slug": slug, "status": "any", "context": "edit"})
        if answer.status != 200 or not isinstance(answer.data, list):
            from ..core.errors import SourceError
            raise SourceError(f"cms_api: поиск записи {slug} — HTTP {answer.status}; снимок «до» не снят, записи нет")
        posts = answer.data
        if len(posts) > 1:
            from ..core.errors import SourceError
            raise SourceError(f"cms_api: записей со slug {slug} — {len(posts)}, неоднозначно; записи нет")
        return posts[0] if posts else None

    def _state(self, slug: str) -> dict:
        post = self.find(slug)
        if not post:
            return {"exists": False, "content": None, "read_from": f"{POSTS}?slug={slug}"}
        return {"exists": True, "read_from": f"{POSTS}/{post['id']}",
                "content": {"id": post["id"], "title": _raw(post.get("title")), "content": _raw(post.get("content")),
                            "status": post.get("status"), "slug": post.get("slug")}}

    def _get(self, pid) -> dict:
        answer = self.client.call("GET", f"{POSTS}/{pid}", params={"context": "edit"})
        return answer.data if isinstance(answer.data, dict) else {}

    def plan(self, package) -> list[Action]:
        m = package.manifest
        body = {"title": m["title"], "content": package.main.read_text(encoding="utf-8"), "slug": m["slug"]}
        state: dict = {}

        def draft():
            post = self.find(m["slug"])
            if post:
                answer = self.client.call("POST", f"{POSTS}/{post['id']}", json=body)
            else:
                answer = self.client.call("POST", POSTS, json={**body, "status": "draft"})
            state["id"] = (answer.data or {}).get("id")
            return answer

        def draft_ok(answer):
            if answer.status not in (200, 201) or not state.get("id"):
                return False, f"HTTP {answer.status}, id записи нет"
            back = self._get(state["id"])
            ok = (_raw(back.get("title")) == body["title"] and back.get("slug") == body["slug"]
                  and _raw(back.get("content")) == body["content"]
                  and back.get("status") == ("publish" if live else "draft"))
            return ok, f"запись {state['id']}: статус {back.get('status')}, заголовок " + ("совпал" if ok else "другой")

        def publish():
            pid = state.get("id") or (self.find(m["slug"]) or {}).get("id")
            if not pid:
                raise EngineError(f"публикация {m['slug']}: черновика нет — сначала действие черновика")
            state["id"] = pid
            return self.client.call("POST", f"{POSTS}/{pid}", json={"status": "publish"})

        def publish_ok(answer):
            back = self._get(state["id"])
            ok = answer.status == 200 and back.get("status") == "publish"
            return ok, f"запись {state['id']}: статус {back.get('status')}"

        snap = lambda: self._state(m["slug"])            # noqa: E731
        existing = self.find(m["slug"])
        live = bool(existing and existing.get("status") == "publish")
        return [Action("cms_draft", f"{self.client.base_url}/{POSTS}", "POST",
                       ("правка опубликованной записи " if live else "черновик ") + m["slug"], "read_back",
                       ("publish", "publish" if live else "commit"), do=draft, verify=draft_ok, snapshot=snap),
                Action("cms_publish", f"{self.client.base_url}/{POSTS}", "POST", f"публикация {m['slug']}",
                       "read_back", ("publish", "publish"), do=publish, verify=publish_ok, snapshot=snap)]

    def restore(self, snapshots: list[dict]) -> list[Action]:
        out = []
        first = {}
        for snap in snapshots:                          # оба действия темы снимают одну запись — первый снимок темы
            first.setdefault(snap["id"], snap)
        for snap in first.values():
            slug = (snap["content"] or {}).get("slug") or snap["read_from"].rsplit("slug=", 1)[-1]
            before = snap["content"]

            def do(before=before, slug=slug):
                post = self.find(slug)
                if not post:
                    return None
                if before is None:
                    return self.client.call("DELETE", f"{POSTS}/{post['id']}")
                return self.client.call("POST", f"{POSTS}/{post['id']}",
                                        json={k: before[k] for k in ("title", "content", "status", "slug")})

            def ok(_, before=before, slug=slug):
                post = self.find(slug)
                if before is None:
                    gone = post is None or post.get("status") == "trash"
                    return gone, f"запись {slug}: " + ("убрана" if gone else f"всё ещё {post.get('status')}")
                same = bool(post) and all((_raw(post.get(k)) if k in ("title", "content") else post.get(k))
                                          == before[k] for k in ("title", "content", "status", "slug"))
                return same, f"запись {slug}: " + ("как в снимке «до»" if same else "отличается от снимка")

            out.append(Action("cms_restore", f"{self.client.base_url}/{POSTS}", "POST/DELETE",
                              f"откат записи {slug} к снимку {snap['run_id']}", "read_back", ("publish", "publish"),
                              do=do, verify=ok, snapshot=lambda slug=slug: self._state(slug)))
        return out


def _raw(field) -> str:
    if isinstance(field, dict):
        return field.get("raw", field.get("rendered", ""))
    return field or ""
