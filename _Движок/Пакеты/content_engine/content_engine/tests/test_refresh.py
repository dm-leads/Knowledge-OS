"""Дожим и актуализация (Д4, Т36, П55): кандидаты по правилам профиля, «нет данных» вместо пустого списка, правка той же
очередью с неизменяемым эталоном, запрет пустого сдвига даты, публикация правки в ту же запись с откатом."""
import json
from datetime import timedelta

import pytest

from content_engine.cli import main
from content_engine.core import bets, store
from content_engine.core.errors import EngineError, GateRefused
from content_engine.core.instance import Instance
from content_engine.core.urls import norm_url
from content_engine.steps import dedup, gate_article, refresh
from content_engine.steps import publish as publish_mod
from content_engine.steps import queue as queue_mod

from .conftest import GREENHOUSE, TODAY, set_profile
from .site_fixture import Page
from .test_publish import cms  # noqa: F401 — фикстура подменённой CMS

URL = "https://example.ru/blog/otoplenie-teplicy"
HTML = ("<html><body><article><h1>Отопление теплицы зимой</h1>"
        + "".join(f"<h2>Раздел {i}</h2><p>{GREENHOUSE}</p>" for i in range(3)) + "</article></body></html>")


def pos(query, position, impressions, *, url=URL, day=TODAY.isoformat()):
    return {"date": day, "engine": "google", "query": query, "url": url, "position": position,
            "impressions": impressions, "period_from": "2026-02-20", "period_to": "2026-02-26", "source": "gsc",
            "status": "факт"}


def write_positions(instance, rows):
    store.append_jsonl(instance.file("positions"), rows)


class Site:
    def __init__(self, status=200, html=HTML):
        self.status, self.html, self.log = status, html, []

    def __call__(self, method, url, **kw):
        self.log.append(url)
        return Page(self.status, self.html if self.status == 200 else "")


def inst(project, transport=None, today=TODAY):
    return Instance(project, today=today, transport=transport)


def test_zone_candidate_by_impressions(project):
    i = inst(project)
    write_positions(i, [pos("отопление теплицы", 6.0, 80), pos("теплица зимой", 9.0, 20),
                        pos("печь в теплице", 2.0, 500, url="https://example.ru/blog/gazon-vesnoj")])
    out = refresh.scan(i)
    zone = [c for c in out["candidates"] if any(r["kind"] == "zone" for r in c["reasons"])]
    assert [c["url"] for c in zone] == [norm_url(URL)]                     # 80 показов ≥ 50 — да; 20 — нет; позиция 2 — вне зоны
    reasons = [r for r in zone[0]["reasons"] if r["kind"] == "zone"]
    assert len(reasons) == 1 and reasons[0]["impressions"] == 80
    assert json.loads((project.parent / "data" / "refresh.json").read_text(encoding="utf-8"))["records"]


def test_zone_without_impressions_is_no_data(project):
    i = inst(project)
    write_positions(i, [pos("отопление теплицы", 6.0, None)])
    out = refresh.scan(i)
    assert not any(r["kind"] == "zone" for c in out["candidates"] for r in c["reasons"])
    assert any(x["kind"] == "zone" and "показов" in x["why"] for x in out["no_data"])


def test_stale_positions_no_data(project):
    i = inst(project)
    old = (TODAY - timedelta(days=9)).isoformat()
    write_positions(i, [pos("отопление теплицы", 6.0, 80, day=old)])
    out = refresh.scan(i)
    assert out["blocks"]["zone"]["status"] == "нет данных" and old in out["blocks"]["zone"]["why"]


def test_no_positions_at_all_is_no_data_not_empty(project):
    out = refresh.scan(inst(project))
    assert out["blocks"]["zone"]["status"] == "нет данных" and "pulse run" in out["blocks"]["zone"]["why"]


def test_old_article_by_day_and_no_date(project):
    i = inst(project)
    with bets.update(i) as box:
        box["records"] += [{"id": "T1", "url": URL, "published": (TODAY - timedelta(days=200)).isoformat()},
                           {"id": "T2", "url": "https://example.ru/blog/ukrytie-roz",
                            "published": (TODAY - timedelta(days=200)).isoformat(),
                            "refreshed": [{"day": (TODAY - timedelta(days=10)).isoformat(), "id": "refresh-x"}]}]
    out = refresh.scan(i)
    aged = {c["url"] for c in out["candidates"] if any(r["kind"] == "age" for r in c["reasons"])}
    assert aged == {norm_url(URL)}                                         # правленая 10 дней назад — не старая
    no_date = {x["url"] for x in out["no_data"] if x["kind"] == "age"}
    assert norm_url("https://example.ru/blog/gazon-vesnoj") in no_date     # ни книги, ни даты в шапке — «нет данных»


@pytest.fixture
def serp_ok(monkeypatch):
    def serp(self, phrases):
        return {"msk": {p: object() for p in phrases}}
    monkeypatch.setattr(dedup.Context, "serp", serp)
    monkeypatch.setattr(queue_mod, "save_serp", lambda instance, tid, formulations: "снимок (подмена)")


def taken(project, site, url=URL):
    i = inst(project, site)
    write_positions(i, [pos("отопление теплицы", 6.0, 80, url=url)])
    return refresh.take(i, url)


def test_take_baseline_immutable_and_hashed(project, serp_ok):
    out = taken(project, Site())
    i = inst(project)
    topic = queue_mod._find(queue_mod.load(i), out["id"])
    assert topic["type"] == "refresh" and topic["status"] == queue_mod.TAKEN and topic["slug"] == "otoplenie-teplicy"
    base = refresh.baseline_of(i, topic)
    assert base["url"] == norm_url(URL) and "Чем топить теплицу" in base["text"]
    path = topic["baseline"]["path"]
    data = json.loads(open(path, encoding="utf-8").read())
    data["text"] = data["text"].replace("Печь", "Камин")
    open(path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
    with pytest.raises(EngineError, match="не совпал с суммой"):
        refresh.baseline_of(i, topic)
    with pytest.raises(GateRefused, match="уже снят"):
        refresh.save_baseline(inst(project, Site()), out["id"], URL)


def test_take_twice_refused(project, serp_ok):
    taken(project, Site())
    with pytest.raises(GateRefused, match="уже в очереди"):
        taken(project, Site())


def test_take_weekly_limit(project, serp_ok):
    set_profile(project, lambda d: d["refresh"].update(weekly_limit=1))
    taken(project, Site())
    with pytest.raises(GateRefused, match="weekly_limit"):
        taken(project, Site(), url="https://example.ru/blog/ukrytie-roz")


def test_take_dead_page_refused(project, serp_ok):
    with pytest.raises(GateRefused, match="HTTP 404"):
        taken(project, Site(status=404))
    assert not queue_mod.load(inst(project))


def test_take_site_down_is_unverified(project, serp_ok):
    import requests

    def down(method, url, **kw):
        raise requests.exceptions.ConnectionError("нет сети")
    i = inst(project, down)
    write_positions(i, [pos("отопление теплицы", 6.0, 80)])
    assert main(["refresh", "take", URL, "--profile", str(project)]) == 3
    assert not queue_mod.load(i)


def _gate(project, out, body):
    i = inst(project)
    path = i.brief_dir(out["id"]) / "article.md"
    path.write_text("---\ntitle: Отопление теплицы зимой\n---\n\n" + body, encoding="utf-8", newline="\n")
    report = gate_article.check_article(i, path, offline=True, topic_id=out["id"])
    return next(r for r in report["checks"] if r["check"] == "refresh_change")


def test_empty_refresh_refused_by_gate(project, serp_ok):
    out = taken(project, Site())
    check = _gate(project, out, GREENHOUSE + "\n\nОбновлено: 2 марта 2026 года.")
    assert not check["ok"] and check["level"] == "block" and "пустое обновление" in check["detail"]
    new = GREENHOUSE + "\n\n## Сколько стоит отопление теплицы\n\n" + " ".join(
        f"Расход дров на квадратный метр теплицы в месяц номер {n} зависит от утепления стен и крыши." for n in range(40))
    assert _gate(project, out, new)["ok"]


def test_ordinary_topic_has_no_refresh_check(project):
    i = inst(project)
    queue_mod.save(i, [{"id": "T9", "topic": "x", "formulations": ["x"], "status": queue_mod.TAKEN}])
    path = i.brief_dir("T9") / "article.md"
    path.write_text("# Заголовок\n\nТекст.\n", encoding="utf-8", newline="\n")
    report = gate_article.check_article(i, path, offline=True, topic_id="T9")
    assert not any(r["check"] == "refresh_change" for r in report["checks"])


def test_mark_refreshed_no_new_record(project):
    i = inst(project)
    with bets.update(i) as box:
        box["records"].append({"id": "T1", "url": URL, "published": "2025-01-01"})
    topic = {"id": "refresh-otoplenie-teplicy", "type": "refresh"}
    added, _ = bets.mark_refreshed(i, topic, URL, TODAY.isoformat())
    again, text = bets.mark_refreshed(i, topic, URL, TODAY.isoformat())
    records = bets.load(i)
    assert added and not again and "уже" in text
    assert len(records) == 1 and records[0]["refreshed"] == [{"day": TODAY.isoformat(), "id": topic["id"]}]
    none, text = bets.mark_refreshed(i, topic, "https://example.ru/blog/net", TODAY.isoformat())
    assert not none and "записи страницы нет" in text and len(bets.load(i)) == 1


def _refresh_topic(project, transport):
    from .test_rights import NOW, SLUG, TID
    i = Instance(project, now=NOW, transport=transport)
    queue_mod.save(i, [{"id": TID, "type": "refresh", "page": f"example.ru/blog/{SLUG}", "slug": SLUG,
                        "topic": "посадка чеснока", "formulations": ["посадка чеснока"], "status": queue_mod.TAKEN}])
    return i, TID


def test_refresh_publish_edits_same_post_and_rollback_restores(cms):
    from content_engine.core import actions as actions_mod
    from content_engine.core import snapshots
    from content_engine.publishers.cms_api import CmsApiPublisher
    from .test_rights import SLUG
    project, wp = cms
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    wp.posts[1] = {"id": 1, "slug": SLUG, "status": "publish", "title": {"raw": "Посадка чеснока под зиму"},
                   "content": {"raw": "Старый текст статьи."}}
    i, tid = _refresh_topic(project, wp)
    assert publish_mod.apply_publish(i, tid, True, transport=wp) == 0
    assert len(wp.posts) == 1 and wp.posts[1]["content"]["raw"] != "Старый текст статьи."
    restore = CmsApiPublisher(i, transport=wp).restore(snapshots.load(i, tid=tid))
    actions_mod.run(i, tid, restore, apply=True, echo=lambda s: None)
    assert wp.posts[1]["content"]["raw"] == "Старый текст статьи." and wp.posts[1]["status"] == "publish"


def test_refresh_publish_refused_when_page_gone(cms):
    project, wp = cms
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    i, tid = _refresh_topic(project, wp)
    with pytest.raises(GateRefused):
        publish_mod.apply_publish(i, tid, True, transport=wp)
    assert wp.posts == {} and not any(m == "POST" for m, _ in wp.log)


def test_refresh_publish_refused_when_slug_differs(cms):
    project, wp = cms
    set_profile(project, lambda d: d["publish"].update(allow="publish"))
    from .test_rights import SLUG
    wp.posts[1] = {"id": 1, "slug": SLUG, "status": "publish", "title": {"raw": "Посадка чеснока под зиму"},
                   "content": {"raw": "Старый текст статьи."}}
    i, tid = _refresh_topic(project, wp)
    topics = queue_mod.load(i)
    topics[0]["slug"] = "drugaya-stranica"
    queue_mod.save(i, topics)
    with pytest.raises(GateRefused):
        publish_mod.apply_publish(i, tid, True, transport=wp)
    assert not any(m == "POST" for m, _ in wp.log)
    assert wp.posts[1]["content"]["raw"] == "Старый текст статьи."


def test_refresh_gaps_against_baseline(project, serp_ok):
    from content_engine.steps.prepare import _refresh_gaps
    out = taken(project, Site())
    i = inst(project)
    topic = queue_mod._find(queue_mod.load(i), out["id"])
    top = [{"url": f"other{n}.ru/a", "h2": ["Раздел 0", "Сколько стоит отопление"], "h3": [], "chars": 20000,
            "tables": 1, "faq": True, "images": 2, "lists": 1} for n in range(3)]
    got = _refresh_gaps(i, topic, top)
    kinds = {g["kind"] for g in got["gaps"]}
    assert kinds == {"раздел", "таблица", "FAQ", "объём"}
    assert [g["what"] for g in got["gaps"] if g["kind"] == "раздел"] == ["Сколько стоит отопление"]
    assert _refresh_gaps(i, topic, [])["status"] == "нет данных"


def test_brief_slug_of_refresh_must_be_page_slug(project, serp_ok):
    import yaml
    from content_engine.steps import brief as brief_mod
    out = taken(project, Site())
    i = inst(project)
    folder = i.brief_dir(out["id"])
    data = {"h1": "Отопление теплицы зимой", "title": "Отопление теплицы зимой: чем топить и сколько стоит",
            "description": "x" * 140, "slug": "drugoj-slug", "angle": "a", "level": "обычный",
            "sections": [{"h2": "Чем топить"}, {"h2": "Сколько стоит"}], "faq": [], "author": "", "links": [],
            "read_also": [], "bridge": {}, "cta_place": "в конце"}
    (folder / "brief.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    errors = brief_mod.check(i, out["id"])["errors"]
    assert any("как у живой страницы" in e for e in errors)
    assert not any("почти дублирует" in e for e in errors)      # заголовок самой правимой страницы — не дубль
    data["slug"] = "otoplenie-teplicy"
    (folder / "brief.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert not any("как у живой страницы" in e for e in brief_mod.check(i, out["id"])["errors"])
