"""Разведка конкурентов (А4, Т41.2, П60): новые адреса за 14 дней по двум снимкам карт — промежуток и статус; первый
снимок — «нет данных» с днём сравнения; без доменов — «не подключено»; разбор устройства новых страниц."""
from datetime import timedelta

import pytest

from content_engine.core.errors import GateRefused
from content_engine.core.instance import Instance
from content_engine.steps import competitors as comp

from .conftest import TODAY, set_profile
from .site_fixture import Page


def urlset(pairs):
    body = "".join(f"<url><loc>{u}</loc>" + (f"<lastmod>{m}</lastmod>" if m else "") + "</url>" for u, m in pairs)
    return f'<?xml version="1.0"?><urlset>{body}</urlset>'


class Rival:
    def __init__(self, pairs, *, index=False, robots=True):
        self.pairs, self.index, self.robots = pairs, index, robots
        self.log = []

    def __call__(self, method, url, **kw):
        self.log.append(url)
        if url.endswith("/robots.txt"):
            return Page(200, "User-agent: *\nSitemap: https://rival.ru/sitemap.xml\n") if self.robots else Page(404)
        if url == "https://rival.ru/sitemap.xml":
            if self.index:
                return Page(200, "<sitemapindex><sitemap><loc>https://rival.ru/s1.xml</loc></sitemap>"
                                 "<sitemap><loc>https://cdn.other.ru/s2.xml</loc></sitemap></sitemapindex>")
            return Page(200, urlset(self.pairs))
        if url == "https://rival.ru/s1.xml":
            return Page(200, urlset(self.pairs))
        if url.startswith("https://rival.ru/blog/"):
            return Page(200, '<html><body><article><h1>Статья</h1><h2>Раздел</h2><p>' + "текст " * 300 +
                             '</p><table><tr><td>1</td></tr></table><img src="a.jpg" alt="a"><h2>Частые вопросы</h2>'
                             '</article><script type="application/ld+json">{"@type": "Article"}</script></body></html>')
        return Page(404)


@pytest.fixture
def rival(project):
    set_profile(project, lambda d: d["search"].update(competitors=["rival.ru"]))
    return project


def snap_on(project, day, pairs, **kw):
    site = Rival(pairs, **kw)
    return comp.snap(Instance(project, today=day, transport=site)), site


def test_competitors_not_connected(project):
    set_profile(project, lambda d: d["search"].update(competitors=[]))
    assert comp.snap(Instance(project, today=TODAY))["status"] == "не подключено"


def test_first_snapshot_no_data_with_date(rival):
    out, _ = snap_on(rival, TODAY, [("https://rival.ru/blog/a", "")])
    assert out["compare"]["status"] == "нет данных" and (TODAY + timedelta(days=14)).isoformat() in out["compare"]["why"]


def test_new_urls_between_two_snapshots_proxy(rival):
    old = [("https://rival.ru/blog/a", ""), ("https://rival.ru/blog/b", "")]
    snap_on(rival, TODAY - timedelta(days=14), old)
    snap_on(rival, TODAY - timedelta(days=3), old)                  # моложе окна — не база сравнения
    out, _ = snap_on(rival, TODAY, old + [("https://rival.ru/blog/c", ""), ("https://rival.ru/blog/d", "")])
    cmp = out["compare"]
    row = cmp["rows"][0]
    assert cmp["from"] == (TODAY - timedelta(days=14)).isoformat() and cmp["days"] == 14
    assert row["new"] == 2 and row["status"] == "proxy" and row["pace_per_week"] == 1.0


def test_reliable_lastmod_is_estimate(rival):
    base = TODAY - timedelta(days=15)
    old = [(f"https://rival.ru/blog/p{n}", f"2025-0{1 + n % 9}-1{n % 9}") for n in range(6)]
    snap_on(rival, base, old)
    out, _ = snap_on(rival, TODAY, old + [("https://rival.ru/blog/new", (TODAY - timedelta(days=2)).isoformat())])
    row = out["compare"]["rows"][0]
    assert row["status"] == "оценка" and row["lastmod_inside"] == 1


def test_same_lastmod_everywhere_is_proxy(rival):
    same = [(f"https://rival.ru/blog/p{n}", "2026-01-01") for n in range(6)]
    snap_on(rival, TODAY - timedelta(days=15), same)
    out, _ = snap_on(rival, TODAY, same + [("https://rival.ru/blog/new", "2026-01-01")])
    row = out["compare"]["rows"][0]
    assert row["status"] == "proxy" and row["new"] == 1 and row["lastmod_inside"] == 0


def test_sitemap_index_followed_and_foreign_map_skipped(rival):
    out, site = snap_on(rival, TODAY, [("https://rival.ru/blog/a", "")], index=True)
    rec = comp.store.read_json(rival.parent / "data" / "competitors" / f"{TODAY.isoformat()}.json",
                               "competitors")["records"][0]
    assert rec["status"] == "ок" and "rival.ru/blog/a" in rec["urls"]
    assert any("вне домена" in s for s in rec["skipped"]) and not any("cdn.other.ru" in u for u in site.log)


def test_pages_structure_parsed(rival):
    old = [("https://rival.ru/blog/a", "")]
    snap_on(rival, TODAY - timedelta(days=14), old)
    _, site = snap_on(rival, TODAY, old + [("https://rival.ru/blog/new", "")])
    with pytest.raises(GateRefused):
        comp.pages(Instance(rival, today=TODAY + timedelta(days=1), transport=site))
    out = comp.pages(Instance(rival, today=TODAY, transport=site))
    page = out["pages"][0]
    assert page["status"] == "разобрано" and page["h2"] == 2 and page["tables"] == 1 and page["images"] >= 1
    assert page["faq"] and page["schema"] == ["Article"] and page["chars"] > 1000
