"""Публикация двумя режимами (Т21, П27, П30): ручной пакет, финальная чистка, ритм, CMS черновиком и публикацией."""
import json
from datetime import timedelta

import pytest

from content_engine.assemblers.base import Package
from content_engine.cli import main
from content_engine.core import actions as actions_mod
from content_engine.core import approval, publications, rights
from content_engine.core.instance import Instance
from content_engine.publishers.cms_api import CmsApiPublisher
from content_engine.steps import publish as publish_mod

from .conftest import set_profile
from .site_fixture import Page, brief_yaml, make_package
from .test_rights import NOW, SLUG, TID, direct, site  # noqa: F401 — фикстуры синтетического сайта


def test_publish_without_approved_brief_refused(direct, site):  # noqa: F811
    folder = Instance(direct, now=NOW, transport=site).brief_dir(TID)
    (folder / "approval.json").unlink()
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 1
    assert main(["publish", "prepare", TID, "--profile", str(direct)]) == 1
    assert site.live(SLUG) is None


def test_manual_prepare_writes_paste_and_rollback_into_manifest(direct, site):  # noqa: F811
    assert main(["publish", "prepare", TID, "--profile", str(direct)]) == 0
    package = Package.load(Instance(direct, now=NOW, transport=site).brief_dir(TID) / "package")
    assert {"paste.md", "rollback.md"} <= set(package.manifest["files"])
    paste = (package.folder / "paste.md").read_text(encoding="utf-8")
    assert "https://example.ru/blog/posadka-chesnoka" in paste and "publish verify" in paste
    assert "Снять статью" in (package.folder / "rollback.md").read_text(encoding="utf-8")


def test_final_clean_catches_broken_internal_link_and_read_also(direct, site):  # noqa: F811
    inst = Instance(direct, now=NOW, transport=site)
    folder = inst.brief_dir(TID) / "package"
    make_package(folder, TID, SLUG, "Посадка чеснока под зиму",
                 'Смотрите <a href="/blog/net-takoj">разбор</a>.', read_also=["https://example.ru/blog/chuzhaya"])
    approval.write(inst, TID, "Человек")
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 1
    problems = publish_mod.final_clean(inst, TID, Package.load(folder), transport=site)
    assert any("net-takoj" in p and "404" in p for p in problems)
    assert any("Читайте также" in p and "chuzhaya" in p for p in problems)
    assert site.live(SLUG) is None


def test_next_respects_rhythm(direct, site):  # noqa: F811
    from content_engine.steps import queue as queue_mod
    from .test_queue import topic
    set_profile(direct, lambda d: d["queue"].update(per_day=0.5))
    inst = Instance(direct, now=NOW, transport=site)
    queue_mod.save(inst, [topic(TID, status=queue_mod.TAKEN)])
    publications.log(inst, "published", id="T0000", url="example.ru/blog/vchera", day=(NOW - timedelta(days=1))
                     .date().isoformat(), built="2026-02-28")
    out = publish_mod.next_topic(inst)
    assert out["id"] is None and "по ритму рано" in out["why"]
    set_profile(direct, lambda d: d["queue"].update(per_day=1))
    out = publish_mod.next_topic(Instance(direct, now=NOW, transport=site))
    assert out["id"] == TID and "publish apply" in out["how"]
    late = Instance(direct, now=NOW.replace(hour=21), transport=site)
    assert "вне окна" in publish_mod.next_topic(late)["why"]


class WordPress:
    """Подмена REST WordPress: записи в памяти, журнал запросов."""

    def __init__(self):
        self.posts, self.log = {}, []

    def __call__(self, method, url, params=None, json=None, **kw):
        import json as js
        self.log.append((method, url))
        path = url.split("/wp-json/wp/v2/", 1)[1]
        if method == "GET" and path == "posts":
            found = [p for p in self.posts.values() if p["slug"] == (params or {}).get("slug")]
            return Page(200, js.dumps(found))
        if method == "POST" and path == "posts":
            pid = len(self.posts) + 1
            self.posts[pid] = {"id": pid, "slug": json["slug"], "status": json.get("status", "draft"),
                               "title": {"raw": json["title"]}, "content": {"raw": json["content"]}}
            return Page(201, js.dumps(self.posts[pid]))
        pid = int(path.split("/")[1])
        post = self.posts[pid]
        if method == "GET":
            return Page(200, js.dumps(post))
        if method == "DELETE":
            post["status"] = "trash"
            return Page(200, js.dumps(post))
        for key in ("title", "content"):
            if key in json:
                post[key] = {"raw": json[key]}
        for key in ("status", "slug"):
            if key in json:
                post[key] = json[key]
        return Page(200, js.dumps(post))


@pytest.fixture
def cms(project, monkeypatch):
    monkeypatch.setenv("CMS_USER", "робот")
    monkeypatch.setenv("CMS_TOKEN", "пароль-приложения")
    set_profile(project, lambda d: d["publish"].update(
        mode="direct", adapter="cms_api", allow="commit", hosts=["example.ru"],
        cms={"base_url": "https://example.ru", "user_env": "CMS_USER", "token_env": "CMS_TOKEN"}))
    wp = WordPress()
    inst = Instance(project, now=NOW, transport=wp)
    folder = inst.brief_dir(TID)
    brief_yaml(folder)
    approval.write(inst, TID, "Человек")
    make_package(folder / "package", TID, SLUG, "Посадка чеснока под зиму", "Текст статьи.")
    return project, wp


def test_cms_draft_and_publish_are_two_actions_by_level(cms):
    project, wp = cms
    inst = Instance(project, now=NOW, transport=wp)
    package = publish_mod.package_of(inst, TID)
    planned = CmsApiPublisher(inst, transport=wp).plan(package)
    assert [a.kind for a in planned] == ["cms_draft", "cms_publish"]
    allowed, refused = rights.split(inst, planned)
    assert [a.kind for a in allowed] == ["cms_draft"] and "publish.allow = publish" in refused[0]
    out = actions_mod.run(inst, TID, allowed, apply=True, echo=lambda s: None)
    assert out.confirmed == 1 and wp.posts[1]["status"] == "draft"
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    inst = Instance(project, now=NOW, transport=wp)
    planned = CmsApiPublisher(inst, transport=wp).plan(package)
    out = actions_mod.run(inst, TID, rights.split(inst, planned)[0], apply=True, echo=lambda s: None)
    assert out.confirmed == 2 and wp.posts[1]["status"] == "publish" and len(wp.posts) == 1


def test_cms_without_apply_no_writes_and_rollback_trashes_new_post(cms):
    project, wp = cms
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    inst = Instance(project, now=NOW, transport=wp)
    publisher = CmsApiPublisher(inst, transport=wp)
    package = publish_mod.package_of(inst, TID)
    actions_mod.run(inst, TID, publisher.plan(package), apply=False, echo=lambda s: None)
    assert publisher.client.writes == 0 and wp.posts == {}
    actions_mod.run(inst, TID, publisher.plan(package), apply=True, echo=lambda s: None)
    snaps = json.loads(next((inst.root / "data" / "snapshots" / "before" / TID).rglob("001.json"))
                       .read_text(encoding="utf-8"))
    assert snaps["exists"] is False
    from content_engine.core import snapshots
    restore = publisher.restore(snapshots.load(inst, tid=TID))
    actions_mod.run(inst, TID, restore, apply=True, echo=lambda s: None)
    assert wp.posts[1]["status"] == "trash"


def test_cms_host_outside_list_not_sent(cms):
    project, wp = cms
    set_profile(project, lambda d: d["publish"].update(hosts=["other.example"]))
    inst = Instance(project, now=NOW, transport=wp)
    from content_engine.core.errors import EndpointNotAllowed
    with pytest.raises(EndpointNotAllowed):
        CmsApiPublisher(inst, transport=wp).plan(publish_mod.package_of(inst, TID))
    assert wp.log == []
