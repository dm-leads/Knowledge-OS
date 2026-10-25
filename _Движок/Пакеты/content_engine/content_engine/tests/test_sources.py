"""Адаптеры спроса, корпуса и ценности страницы (Т4.2–Т4.4) на синтетических ответах."""
import json
from datetime import date

import pytest

from content_engine.adapters.corpus import markdown_folder, reconcile, sitemap_urls
from content_engine.adapters.demand import FileDemand, value_of
from content_engine.adapters.fake import FakeSerp
from content_engine.adapters.gsc import SearchConsole
from content_engine.adapters.keysso import KeyssoDemand
from content_engine.adapters.serp import SiteKeyword
from content_engine.adapters.value import PageValue
from content_engine.adapters.webmaster import Webmaster
from content_engine.core.instance import Instance
from content_engine.core.urls import norm_url

from .conftest import TODAY, set_profile
from .keysso_transport import FIXTURE, Response


def test_search_console_queries_and_pages():
    seen = []

    def transport(method, url, params=None, json=None, headers=None, timeout=None):
        seen.append(json["dimensions"])
        if json["dimensions"] == ["query"]:
            return Response(200, {"rows": [{"keys": ["полив теплицы"], "impressions": 40, "clicks": 2,
                                            "position": 8.5}]})
        return Response(200, {"rows": [{"keys": ["https://example.ru/blog/otoplenie-teplicy", "полив теплицы"],
                                        "impressions": 30, "clicks": 1, "position": 7.0}]})

    gsc = SearchConsole("sc-domain:example.ru", "токен", "2026-03-01", transport=transport)
    rows = gsc.fetch("2026-01-30", "2026-02-28")
    pages = gsc.fetch_pages("2026-01-30", "2026-02-28")
    assert value_of(rows[0], "impressions") == 40 and rows[0]["numbers"]["impressions"]["status"] == "факт"
    assert pages[0]["page"] == "https://example.ru/blog/otoplenie-teplicy" and pages[0]["query"] == "полив теплицы"
    assert seen == [["query"], ["page", "query"]]


def test_webmaster_popular_queries():
    def transport(method, url, params=None, json=None, headers=None, timeout=None):
        assert headers["Authorization"].startswith("OAuth ")
        return Response(200, {"queries": [{"query_text": "отопление теплицы",
                                           "indicators": {"TOTAL_SHOWS": 55, "TOTAL_CLICKS": 4,
                                                          "AVG_SHOW_POSITION": 6.1}}]})

    rows = Webmaster("1", "https:example.ru:443", "токен", "2026-03-01", transport=transport).fetch(
        "2026-01-30", "2026-02-28")
    assert rows[0]["query"] == "отопление теплицы" and value_of(rows[0], "impressions") == 55


def test_keysso_demand_own_and_competitors():
    demand = KeyssoDemand(FakeSerp(FIXTURE), "example.ru", "msk", ["garden.example"], "2026-03-01",
                          site_limit=100, competitor_limit=100)
    rows = demand.fetch("2026-01-30", "2026-02-28")
    own = [r for r in rows if r["page"]]
    rivals = [r for r in rows if r.get("competitor")]
    assert len(own) == 5 and len(rivals) == 2
    assert own[0]["numbers"]["frequency"]["status"] == "оценка"


def test_demand_file(tmp_path):
    path = tmp_path / "core.csv"
    path.write_text("query;impressions;frequency;source\nполив теплицы;12;300;ядро\n;;;\n", encoding="utf-8")
    rows = FileDemand(path, "2026-03-01").fetch("2026-01-30", "2026-02-28")
    assert len(rows) == 1 and value_of(rows[0], "frequency") == 300


def test_corpus_fields_from_sidecar_files(project):
    root = project.parent
    folder = root / "issues" / "Отопление"
    folder.mkdir(parents=True)
    (folder / "article.md").write_text("---\ntitle: Отопление\n---\n\n# Отопление теплицы\n\n## Раздел\n\nТекст.\n",
                                       encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"slug": "otoplenie-novoe"}), encoding="utf-8")
    (folder / "row_17.json").write_text("[]", encoding="utf-8")

    def add(data):
        data["corpus"]["folders"].append({
            "path": "issues", "pattern": "*/article.md", "exclude": ["Архив"],
            "fields": {"slug": "slug", "@meta.json:slug": "slug", "@row_*.json": "id", "title": "title"},
            "url_template": "https://{domain}/{section}/{slug}", "section": "blog", "index_csv": ""})

    set_profile(project, add)
    pages = {p.slug: p for p in Instance(project, today=TODAY).corpus()[0]}
    assert pages["otoplenie-novoe"].id == "17"
    assert pages["otoplenie-novoe"].url == "example.ru/blog/otoplenie-novoe"


def test_sitemap_reconcile_by_slug_ignores_section(project, tmp_path):
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text("<urlset><url><loc>https://example.ru/blog/otoplenie-teplicy/</loc></url>"
                       "<url><loc>https://example.ru/blog/poliv-sada</loc></url>"
                       "<url><loc>https://example.ru/blog/novaya-statya</loc></url>"
                       "<url><loc>https://example.ru/catalog/teplicy</loc></url></urlset>", encoding="utf-8")
    set_profile(project, lambda d: d["site"].update(sitemap_file=str(sitemap)))
    instance = Instance(project, today=TODAY)
    urls = sitemap_urls(instance.p, instance.root)
    result = reconcile(instance.corpus()[0], urls, ["blog"])
    assert result["moved_section"] == ["poliv-sada"]          # в шапке раздел garden, на сайте blog — не расхождение
    assert result["only_sitemap"] == ["novaya-statya"]
    assert result["only_corpus"] == ["gazon-vesnoj", "ukrytie-roz"]


def test_url_normalisation():
    assert norm_url("https://xn--e1afmkfd.xn--p1acf/blog/x/") == "пример.рус/blog/x"
    assert norm_url("www.example.ru/a") == norm_url("https://example.ru/a/")


def _value(project, tmp_path, csv_text, check=None, keywords=()):
    path = tmp_path / "money.csv"
    path.write_text(csv_text, encoding="utf-8")

    def change(d):
        d["sources"]["value"].update(kind="file", file=str(path), average_check=check)

    set_profile(project, change)
    instance = Instance(project, today=TODAY)
    return PageValue(instance.p, instance.root, TODAY, site_keywords=list(keywords))


HEAD = "slug;money;leads;period_from;period_to;source;as_of\n"


def test_value_ladder(project, tmp_path):
    keywords = [SiteKeyword("example.ru/blog/gazon-vesnoj", "газон весной", 4, 200, 3.0)]
    value = _value(project, tmp_path, HEAD + "otoplenie-teplicy;5000;2;2025-09-01;2026-02-28;crm;2026-03-01\n"
                   "ukrytie-roz;;3;2025-09-01;2026-02-28;crm;2026-03-01\n", check=1000, keywords=keywords)
    money = value.value("example.ru/blog/otoplenie-teplicy")
    assert money["step"] == 1 and money["money"].value == 5000 and money["money"].status == "факт"
    leads = value.value("example.ru/blog/ukrytie-roz")
    assert leads["step"] == 2 and leads["money"].value == 3000 and leads["money"].status == "оценка"
    proxy = value.value("example.ru/blog/gazon-vesnoj")
    assert proxy["step"] == 3 and proxy["money"].status == "proxy" and proxy["money"].value == 600
    nothing = value.value("example.ru/blog/poliv-sada")
    assert nothing["step"] == 4 and nothing["money"].value is None


def test_value_without_files_is_step_four(project):
    instance = Instance(project, today=TODAY)
    assert PageValue(instance.p, instance.root, date(2026, 3, 2)).value("example.ru/blog/x")["step"] == 4
