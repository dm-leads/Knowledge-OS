"""Права на запись, блокировка, снимок «до» и откат (Т16, П17–П19) — на синтетическом статическом сайте."""
import json
from datetime import datetime, timedelta

import pytest

from content_engine.adapters.write_client import WriteClient
from content_engine.cli import main
from content_engine.core import actions as actions_mod
from content_engine.core import approval, locks, publications, rights
from content_engine.core.errors import EndpointNotAllowed, LockBusy, SnapshotMissing, WriteUnconfirmed
from content_engine.core.instance import Instance
from content_engine.publishers.static_repo import StaticRepoPublisher

from .conftest import set_profile
from content_engine.steps.publish import package_of

from .site_fixture import Page, StaticSite, brief_yaml, git, make_package

NOW = datetime(2026, 3, 2, 11, 0, 0)
TID = "T0001"
WRITE = {"indexing": {"allow": "write"}, "records": {"allow": "write"}, "publish": {"allow": "publish"}}
SLUG = "posadka-chesnoka"


@pytest.fixture
def site(tmp_path):
    return StaticSite(tmp_path)


@pytest.fixture
def direct(project, site, monkeypatch):
    """Профиль прямой публикации в репозиторий синтетического сайта; ступень — по тесту."""
    set_profile(project, lambda d: d["publish"].update(mode="direct", adapter="static_repo",
                                                      repo_path=str(site.work / "blog"), allow="publish",
                                                      hosts=["example.ru", str(site.remote)]))
    import content_engine.cli as cli
    real = cli.open_instance
    monkeypatch.setattr(cli, "open_instance", lambda args, **kw: real(args, now=NOW, transport=site))
    inst = Instance(project, now=NOW, transport=site)
    folder = inst.brief_dir(TID)
    brief_yaml(folder)
    approval.write(inst, TID, "Человек")
    make_package(folder / "package", TID, SLUG, "Посадка чеснока под зиму", "Новый текст статьи о чесноке.")
    return project


def instance(project, site, now=NOW):
    return Instance(project, now=now, transport=site)


def allow(project, level):
    set_profile(project, lambda d: d["publish"].update(allow=level))


def test_files_level_plans_nothing_commit_level_has_no_publish(direct, site):
    allow(direct, "files")
    inst = instance(direct, site)
    planned = StaticRepoPublisher(inst, transport=site).plan(package_of(inst, TID))
    allowed, refused = rights.split(inst, planned)
    assert allowed == [] and any("publish.allow = commit" in r for r in refused)
    allow(direct, "commit")
    inst = instance(direct, site)
    allowed, refused = rights.split(inst, planned)
    assert [a.kind for a in allowed] == ["commit"] and any("publish.allow = publish" in r for r in refused)


def test_apply_with_files_level_is_refused_and_writes_nothing(direct, site):
    allow(direct, "files")
    head = git(site.work, "rev-parse", "HEAD")
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 1
    assert git(site.work, "rev-parse", "HEAD") == head and site.live(SLUG) is None


def test_without_apply_zero_writes(direct, site, capsys):
    head = git(site.work, "rev-parse", "HEAD")
    assert main(["publish", "apply", TID, "--profile", str(direct)]) == 0
    out = capsys.readouterr().out
    assert "запросов записи 0" in out and "план: commit" in out and "план: push" in out
    assert git(site.work, "rev-parse", "HEAD") == head and site.live(SLUG) is None


def test_write_client_host_outside_whitelist_not_sent():
    sent = []
    client = WriteClient("cms", "https://other.example/api", {("POST", "posts")}, hosts=["example.ru"],
                         transport=lambda *a, **k: sent.append(a))
    with pytest.raises(EndpointNotAllowed):
        client.call("POST", "posts", json={})
    client = WriteClient("cms", "https://example.ru/api", {("POST", "posts")}, hosts=["example.ru"],
                         transport=lambda *a, **k: sent.append(a))
    with pytest.raises(EndpointNotAllowed):
        client.call("DELETE", "posts")
    assert sent == [] and client.writes == 0


def test_zero_confirmed_is_code_3(direct, site):
    site.extra["example.ru/blog/" + SLUG] = Page(500, "ошибка")      # сайт не пересобрался
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 3


def test_unconfirmed_action_raises(tmp_path):
    inst = type("I", (), {"now": lambda self: NOW, "root": tmp_path, "p": WRITE})()
    action = actions_mod.Action("indexnow", "https://example.ru", "POST", "заявка", "status_code",
                                ("indexing", "write"), do=lambda: 500, verify=lambda r: (False, "HTTP 500"),
                                needs_snapshot=False)
    with pytest.raises(WriteUnconfirmed, match="записано 1 — подтверждено 0"):
        actions_mod.run(inst, TID, [action], apply=True, echo=lambda s: None)


def test_live_lock_refuses_second_run_old_lock_removed(direct, site):
    inst = instance(direct, site)
    path = locks.acquire(inst, TID, "publish")
    head = git(site.work, "rev-parse", "HEAD")
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 1
    assert git(site.work, "rev-parse", "HEAD") == head
    with pytest.raises(LockBusy):
        locks.acquire(instance(direct, site, NOW + timedelta(minutes=30)), TID, "publish")
    later = instance(direct, site, NOW + timedelta(minutes=61))
    locks.acquire(later, TID, "publish")
    rows = publications.read(later)
    assert rows[-1]["event"] == "lock_removed" and rows[-1]["id"] == TID
    assert path.is_file()


def test_lock_refusal_sends_no_requests(direct, site):
    locks.acquire(instance(direct, site), TID, "publish")
    site.log.clear()
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 1
    assert site.log == []


def test_publish_then_rollback_returns_snapshot(direct, site):
    # до публикации на сайте уже есть старая версия страницы
    (site.work / "blog" / f"{SLUG}.html").write_text("<html><body><h1>Старая версия</h1></body></html>",
                                                     encoding="utf-8", newline="\n")
    git(site.work, "add", "-A")
    git(site.work, "commit", "-m", "старая версия")
    git(site.work, "push")
    before = site.live(SLUG)
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 0
    assert "Посадка чеснока под зиму" in site.live(SLUG)
    snaps = sorted((direct.parent / "data" / "snapshots" / "before" / TID).rglob("*.json"))
    assert [json.loads(p.read_text(encoding="utf-8"))["action"] for p in snaps] == ["commit", "push"]
    assert main(["publish", "rollback", TID, "--profile", str(direct), "--apply"]) == 0
    assert site.live(SLUG) == before
    assert (site.work / "blog" / f"{SLUG}.html").read_text(encoding="utf-8") == before


def test_rollback_of_new_page_removes_it(direct, site):
    assert main(["publish", "apply", TID, "--profile", str(direct), "--apply"]) == 0
    assert site.live(SLUG) is not None
    run_id = publications.read(instance(direct, site))[-1]["run"]
    assert main(["publish", "rollback", "--run", run_id, "--profile", str(direct), "--apply"]) == 0
    assert site.live(SLUG) is None


def test_no_snapshot_no_write():
    inst = type("I", (), {"now": lambda self: NOW, "root": None, "p": WRITE})()
    done = []
    action = actions_mod.Action("db_row", "таблица", "POST", "строка", "read_back", ("records", "write"),
                                do=lambda: done.append(1), verify=lambda r: (True, ""), snapshot=None)
    with pytest.raises(SnapshotMissing):
        actions_mod.run(inst, TID, [action], apply=True, echo=lambda s: None)
    assert done == []


def test_rollback_without_snapshots_refused(direct, site):
    assert main(["publish", "rollback", TID, "--profile", str(direct), "--apply"]) == 1
