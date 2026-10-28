"""Правки по проверке кода 1.5.0 второй моделью: признак правки в пакете, эталон после выдачи, пустая выдача,
публикатор правки только обновляет, сумма всего эталона, заморозка без ложного следа, квота Keys.so без повторов,
шаг воронки без данных, неполный снимок конкурента, равные окна замера, защита дня месяца."""
import json
from datetime import timedelta

import pytest

from content_engine.adapters.keysso import KeyssoAi
from content_engine.assemblers.base import Package, write_manifest
from content_engine.core import actions as actions_mod
from content_engine.core import store
from content_engine.core.errors import EngineError, GateRefused, SourceError, WriteUnconfirmed
from content_engine.core.instance import Instance
from content_engine.publishers.cms_api import CmsApiPublisher
from content_engine.schedulers import base as sched_base
from content_engine.steps import behavior, competitors, dedup, freeze, refresh
from content_engine.steps import publish as publish_mod
from content_engine.steps import queue as queue_mod

from .conftest import TODAY, set_profile
from .keysso_transport import Response
from .test_publish import cms  # noqa: F401 — фикстура подменённой CMS
from .test_refresh import URL, Site, pos, write_positions


def test_refresh_flag_in_package_survives_lost_queue_topic(cms):
    from .test_rights import NOW, SLUG, TID
    project, wp = cms
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    i = Instance(project, now=NOW, transport=wp)
    pkg = Package.load(i.brief_dir(TID) / "package")
    meta = {k: v for k, v in pkg.manifest.items() if k not in ("files", "schema_version", "kind")}
    write_manifest(pkg.folder, {**meta, "refresh_page": f"example.ru/blog/{SLUG}", "refresh_slug": SLUG},
                   list(pkg.manifest["files"]))
    queue_mod.save(i, [])                                     # тему правки потеряли — пакет всё равно «правка»
    with pytest.raises(GateRefused, match="темы правки в очереди нет"):
        publish_mod.apply_publish(i, TID, True, transport=wp)
    assert wp.posts == {}


def test_refresh_canonical_must_be_page(cms):
    from .test_rights import NOW, SLUG, TID
    project, wp = cms
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    wp.posts[1] = {"id": 1, "slug": SLUG, "status": "publish", "title": {"raw": "x"}, "content": {"raw": "старый"}}
    i = Instance(project, now=NOW, transport=wp)
    queue_mod.save(i, [{"id": TID, "type": "refresh", "page": f"example.ru/articles/{SLUG}", "slug": SLUG,
                        "topic": "x", "formulations": ["x"], "status": queue_mod.TAKEN}])
    with pytest.raises(GateRefused, match="не совпал со страницей"):
        publish_mod.apply_publish(i, TID, True, transport=wp)
    assert wp.posts[1]["content"]["raw"] == "старый"


def test_update_only_never_creates_post_even_if_deleted_after_snapshot(cms):
    from .test_rights import NOW, TID
    project, wp = cms
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    i = Instance(project, now=NOW, transport=wp)
    package = publish_mod.package_of(i, TID)
    planned = CmsApiPublisher(i, transport=wp).plan(package, update_only=True)
    with pytest.raises(WriteUnconfirmed):
        actions_mod.run(i, TID, planned, apply=True, echo=lambda s: None)
    assert wp.posts == {} and not any(m == "POST" and u.endswith("/posts") for m, u in wp.log)


def test_failed_serp_leaves_no_baseline_and_retry_works(project, monkeypatch):
    calls = {"n": 0}

    def serp(self, phrases):
        calls["n"] += 1
        return {} if calls["n"] == 1 else {"msk": {p: object() for p in phrases}}
    monkeypatch.setattr(dedup.Context, "serp", serp)
    monkeypatch.setattr(queue_mod, "save_serp", lambda instance, tid, formulations: "снимок (подмена)")
    i = Instance(project, today=TODAY, transport=Site())
    write_positions(i, [pos("отопление теплицы", 6.0, 80)])
    with pytest.raises(GateRefused, match="выдачи"):
        refresh.take(i, URL)                                  # пустая выдача — отказ
    assert not (project.parent / "data" / "refresh" / "baseline").exists()
    assert refresh.take(Instance(project, today=TODAY, transport=Site()), URL)["taken"]


def test_baseline_markers_change_is_detected(project, monkeypatch):
    monkeypatch.setattr(dedup.Context, "serp", lambda self, phrases: {"msk": {p: object() for p in phrases}})
    monkeypatch.setattr(queue_mod, "save_serp", lambda instance, tid, formulations: "снимок (подмена)")
    i = Instance(project, today=TODAY, transport=Site())
    write_positions(i, [pos("отопление теплицы", 6.0, 80)])
    out = refresh.take(i, URL)
    topic = queue_mod._find(queue_mod.load(i), out["id"])
    path = topic["baseline"]["path"]
    data = json.loads(open(path, encoding="utf-8").read())
    data["markers"]["chars"] = 1
    open(path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
    with pytest.raises(EngineError, match="не совпал с суммой"):
        refresh.baseline_of(i, topic)


def test_freeze_refused_accept_leaves_no_decision(project):
    from .test_freeze import _clusters, _history
    i = Instance(project, today=TODAY)
    _clusters(i, {"c2": "полив газона"})
    _history(i, "полив газона", [50] * 24)
    freeze.scan(i)
    with pytest.raises(GateRefused):
        freeze.accept(i, "c2", "Человек")
    assert not (project.parent / "data" / "freeze_decisions.jsonl").exists()


class Flaky:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, method, url, **kw):
        self.calls += 1
        return self.answers.pop(0) if self.answers else Response(200, {"data": [], "last_page": 1, "total": 0})


@pytest.mark.parametrize("first", [Response(500, {}), Response(202, {"code": 202})])
def test_keysso_ai_no_retry_no_polling(first):
    api = Flaky(first, Response(200, {"data": [], "last_page": 1, "total": 0}))
    client = KeyssoAi("ключ", transport=api, sleep=lambda s: None)
    with pytest.raises(SourceError):
        client.answers("example.ru", pages=1)
    assert api.calls == 1                                     # одна единица предела — один запрос


def test_unknown_funnel_step_is_no_data_not_zero(project, monkeypatch):
    class M:
        def entry_pages(self, frm, to):
            return []

        def by_device(self, frm, to):
            return []

        def step_users(self, frm, to, step):
            return None if step == "b" else 300.0
    set_profile(project, lambda d: d["behavior"].update(kind="metrika", token_env="T", counter_env="C",
                                                         funnel=["a", "b", "c"]))
    monkeypatch.setattr(behavior, "_client", lambda instance, transport=None: M())
    out = behavior.scan(Instance(project, today=TODAY))
    assert not out["findings"] and any("«b»" in x for x in out["no_data"])


def test_check_windows_equal_before_and_after(project, monkeypatch):
    from content_engine.core import publications
    seen = []

    class M:
        def entry_pages(self, frm, to):
            seen.append((frm, to))
            return [{"url": "example.ru/blog/a", "users": 150, "visits": 150, "bounce": 0.5, "depth": 1.1, "goals": 0}]

        def by_device(self, frm, to):
            return []

        def step_users(self, frm, to, step):
            return 100.0
    set_profile(project, lambda d: d["behavior"].update(kind="metrika", token_env="T", counter_env="C",
                                                         recheck_days=[14]))
    monkeypatch.setattr(behavior, "_client", lambda instance, transport=None: M())
    pub = TODAY - timedelta(days=20)
    early = Instance(project, today=pub - timedelta(days=2))
    behavior.scan(early)
    behavior.accept(early, "dead_end|example.ru/blog/a", "Человек")
    publications.log(Instance(project, today=pub), "published", id="r", url="example.ru/blog/a", day=pub.isoformat())
    seen.clear()
    behavior.check(Instance(project, today=TODAY))
    from datetime import date
    spans = [(date.fromisoformat(b) - date.fromisoformat(a)).days + 1 for a, b in seen]
    assert spans == [14, 14]


def test_incomplete_competitor_snapshot_is_no_data(project):
    from .test_competitors import Rival
    set_profile(project, lambda d: d["search"].update(competitors=["rival.ru"]))

    class GzOnly(Rival):
        def __call__(self, method, url, **kw):
            from .site_fixture import Page
            if url.endswith("/robots.txt"):
                return Page(200, "Sitemap: https://rival.ru/sitemap.xml.gz\n")
            return super().__call__(method, url, **kw)
    competitors.snap(Instance(project, today=TODAY - timedelta(days=14), transport=GzOnly([])))
    out = competitors.snap(Instance(project, today=TODAY, transport=GzOnly([])))
    row = out["compare"]["rows"][0]
    assert row["status"] == "нет данных" and "сжатая" in row["why"]
    with pytest.raises(GateRefused, match="--limit"):
        competitors.pages(Instance(project, today=TODAY), limit=0)


def test_month_job_without_day_refused():
    with pytest.raises(EngineError, match="month"):
        sched_base.Job("x", "c", "w", "month", "08:00", "", 0)


def test_future_article_date_is_not_fresh(project):
    from content_engine.adapters.corpus import Page, Section
    from content_engine.steps import ai_visibility
    page = Page(slug="a", url="https://example.ru/blog/a", title="", sections=[Section("", "текст")],
                date=(TODAY + timedelta(days=30)).isoformat())
    assert ai_visibility.page_features(Instance(project, today=TODAY), page)["date"] is False
