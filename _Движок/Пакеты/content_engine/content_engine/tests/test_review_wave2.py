"""Находки проверки кода части 1 (З16, 26.10.2026) — каждая закреплена тестом."""
import json
from datetime import timedelta

import pytest
import requests

import content_engine.cli as cli
from content_engine.cli import main
from content_engine.cli import open_instance as REAL_OPEN
from content_engine.core import locks, publications, spend
from content_engine.core.errors import LockBusy
from content_engine.core.instance import Instance
from content_engine.steps import indexing

from .conftest import TODAY
from .site_fixture import git, make_package
from .test_indexing import Net, sent
from .test_indexing import setup as index_setup
from .test_brief import GOOD, inst, put  # noqa: F401
from .test_rights import NOW, SLUG, TID, direct, site  # noqa: F401


def at(monkeypatch, site, minutes):
    monkeypatch.setattr(cli, "open_instance",
                        lambda args, **kw: REAL_OPEN(args, now=NOW + timedelta(minutes=minutes), transport=site))


def test_second_rollback_does_not_republish(direct, site, monkeypatch):  # noqa: F811
    at(monkeypatch, site, 0)
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 0
    at(monkeypatch, site, 1)
    assert main(["publish", "rollback", TID, "--profile", str(direct), "--apply"]) == 0
    assert site.live(SLUG) is None
    folder = direct.parent / "data" / "briefs" / TID
    (folder / "brief.yaml").write_text((folder / "brief.yaml").read_text(encoding="utf-8") + "# правка\n",
                                       encoding="utf-8")
    at(monkeypatch, site, 2)
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 1
    at(monkeypatch, site, 3)
    main(["publish", "rollback", TID, "--profile", str(direct), "--apply"])
    assert site.live(SLUG) is None, "второй откат выложил статью без утверждённого брифа"


def test_commit_takes_only_own_file(direct, site):  # noqa: F811
    other = site.work / "blog" / "chuzhoe.html"
    other.write_text("<html>чужая правка</html>", encoding="utf-8")
    git(site.work, "add", "--", "blog/chuzhoe.html")
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 0
    files = git(site.work, "show", "--name-only", "--format=", "HEAD").split()
    assert files == [f"blog/{SLUG}.html"]
    assert "blog/chuzhoe.html" in git(site.work, "diff", "--cached", "--name-only")   # чужое осталось в индексе


def test_push_refused_with_foreign_unpushed_commits(direct, site):  # noqa: F811
    (site.work / "blog" / "chuzhoe.html").write_text("<html>чужое</html>", encoding="utf-8")
    git(site.work, "add", "-A")
    git(site.work, "commit", "-m", "чужой неотправленный коммит")
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 3
    assert site.live(SLUG) is None and site.live("chuzhoe") is None


class Half(Net):
    fail_bing = True

    def __call__(self, method, url, json=None, **kw):
        if "bing.com" in url and self.fail_bing:
            self.log.append((method, url))
            raise requests.exceptions.ConnectionError("обрыв")
        return super().__call__(method, url, json=json, **kw)


def test_dropped_answer_logged_and_not_resent_same_day(project, monkeypatch):
    net = Half(indexnow=(202, 202, 202))
    inst = index_setup(project, net, monkeypatch=monkeypatch)
    with pytest.raises(Exception):
        indexing.submit(inst, TID, apply=True)
    rows = [r for r in publications.read(inst) if r["event"] == "indexing"]
    assert {r["system"]: r["confirmed"] for r in rows} == {"indexnow_yandex": True, "indexnow_bing": False}
    net.fail_bing = False
    indexing.submit(Instance(project, today=TODAY, transport=net), TID, apply=True)
    assert len([u for u in sent(net) if "yandex.com" in u]) == 1
    assert len([u for u in sent(net) if "bing.com" in u]) == 1


def test_manual_prepare_runs_final_clean(direct, site):  # noqa: F811
    inst = Instance(direct, now=NOW, transport=site)
    make_package(inst.brief_dir(TID) / "package", TID, SLUG, "Посадка чеснока под зиму",
                 'Смотрите <a href="/blog/net-takoj">разбор</a>.')
    assert main(["publish", "prepare", TID, "--profile", str(direct)]) == 1
    assert not (inst.brief_dir(TID) / "package" / "paste.md").exists()


def test_lock_is_atomic_and_release_keeps_foreign_lock(direct, site):  # noqa: F811
    inst = Instance(direct, now=NOW, transport=site)
    path = locks.acquire(inst, TID, "publish")
    with pytest.raises(LockBusy):
        locks.acquire(inst, TID, "publish")
    later = Instance(direct, now=NOW + timedelta(minutes=90), transport=site)
    with locks.held(later, TID, "publish"):
        # пока «поздний» прогон держит блокировку, её перехватил третий процесс
        path.write_text(json.dumps({"time": (NOW + timedelta(minutes=91)).isoformat(), "pid": 1}), encoding="utf-8")
    assert path.is_file(), "снята чужая блокировка"


def test_package_from_old_brief_not_published(direct, site):  # noqa: F811
    from content_engine.assemblers.base import Package, write_manifest
    from content_engine.core import approval
    inst = Instance(direct, now=NOW, transport=site)
    folder = inst.brief_dir(TID) / "package"
    m = Package.load(folder).manifest
    meta = {k: v for k, v in m.items() if k not in ("files", "schema_version", "kind")}
    meta["brief_sha256"] = "0" * 64                     # пакет собран из другого брифа
    write_manifest(folder, meta, list(m["files"]))
    approval.write(inst, TID, "Человек")
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 1
    assert site.live(SLUG) is None


def test_approve_writes_agent_tokens_line(inst, project):  # noqa: F811
    from content_engine.steps import brief as brief_mod
    brief_mod.make_task(inst, TID)
    put(inst, GOOD)
    assert main(["prepare", "approve", TID, "--profile", str(project), "--who", "Человек"]) == 0
    rows = [r for r in spend.rows(Instance(project, today=TODAY)) if r["kind"] == "tokens" and r["id"] == TID]
    assert len(rows) == 1 and rows[0]["status"] == "нет данных" and rows[0]["value"] is None
    put(inst, dict(GOOD, tokens=1200))
    assert main(["prepare", "approve", TID, "--profile", str(project), "--who", "Человек"]) == 0
    rows = [r for r in spend.rows(Instance(project, today=TODAY)) if r["kind"] == "tokens" and r["id"] == TID]
    assert rows[-1]["status"] == "факт" and rows[-1]["value"] == 1200
    assert main(["prepare", "approve", TID, "--profile", str(project), "--who", "Человек"]) == 0
    assert len([r for r in spend.rows(Instance(project, today=TODAY)) if r["kind"] == "tokens"]) == 2


def test_index_and_record_refused_under_live_lock(project, monkeypatch):
    """Приёмка З28 (Codex): заявки и записи дня выкладки идут под блокировкой темы."""
    from content_engine.steps import records as records_mod
    net = Net()
    inst = index_setup(project, net, monkeypatch=monkeypatch)
    locks.acquire(inst, TID, "index")
    with pytest.raises(LockBusy):
        indexing.submit(inst, TID, apply=True)
    assert sent(net) == []
    locks.acquire(inst, TID, "record")
    with pytest.raises(LockBusy):
        records_mod.record(inst, TID)


def test_codex_guards_rights_live_state_slug(direct, site, tmp_path):  # noqa: F811
    """Приёмка З28 (Codex): права проверяет и исполнитель; неизвестное состояние страницы и кривой slug — записи нет."""
    from content_engine.core import actions as actions_mod
    from content_engine.core.errors import EngineError, RightsRefused
    from content_engine.publishers.static_repo import StaticRepoPublisher
    from content_engine.steps.publish import package_of
    from .site_fixture import Page
    from .conftest import set_profile
    set_profile(direct, lambda d: d["publish"].update(allow="commit"))
    inst = Instance(direct, now=NOW, transport=site)
    planned = StaticRepoPublisher(inst, transport=site).plan(package_of(inst, TID))
    with pytest.raises(RightsRefused):                 # push в обход rights.split — отказ самого исполнителя
        actions_mod.run(inst, TID, planned, apply=True, echo=lambda s: None)
    set_profile(direct, lambda d: d["publish"].update(allow="publish"))
    site.extra["example.ru/blog/" + SLUG] = Page(503, "временно недоступно")
    head = git(site.work, "rev-parse", "HEAD")
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 3
    assert git(site.work, "rev-parse", "HEAD") == head     # снимок «до» не снят — ни коммита, ни push
    folder = inst.brief_dir(TID) / "package"
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["slug"] = "../config"
    from content_engine.assemblers.base import Package
    with pytest.raises(EngineError, match="slug"):
        StaticRepoPublisher(inst, transport=site).plan(Package(folder, m))


def test_cms_search_error_means_no_snapshot_no_write(project, monkeypatch):
    from content_engine.core.errors import SourceError
    from content_engine.publishers.cms_api import CmsApiPublisher
    from content_engine.steps.publish import package_of
    from .site_fixture import Page
    from .test_publish import cms as _cms  # noqa: F401
    from .test_publish import WordPress
    monkeypatch.setenv("CMS_USER", "робот")
    monkeypatch.setenv("CMS_TOKEN", "пароль")
    from .conftest import set_profile
    from .site_fixture import brief_yaml
    from content_engine.core import approval
    set_profile(project, lambda d: d["publish"].update(mode="direct", adapter="cms_api", allow="publish",
                hosts=["example.ru"], cms={"base_url": "https://example.ru", "user_env": "CMS_USER",
                                           "token_env": "CMS_TOKEN"}))

    class Broken(WordPress):
        def __call__(self, method, url, params=None, json=None, **kw):
            if method == "GET" and url.endswith("/posts"):
                self.log.append((method, url))
                return Page(500, "ошибка сервера")
            return super().__call__(method, url, params=params, json=json, **kw)
    wp = Broken()
    inst = Instance(project, now=NOW, transport=wp)
    brief_yaml(inst.brief_dir(TID))
    approval.write(inst, TID, "Человек")
    make_package(inst.brief_dir(TID) / "package", TID, SLUG, "Посадка чеснока", "Текст.")
    with pytest.raises(SourceError, match="HTTP 500"):
        CmsApiPublisher(inst, transport=wp).plan(package_of(inst, TID))
    assert all(m == "GET" for m, _ in wp.log) and wp.posts == {}
