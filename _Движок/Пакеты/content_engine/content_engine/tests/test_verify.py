"""«Доехало» по ответу сайта (Т22, П31–П33): эталон — манифест или запись корпуса, день выкладки, снимок, вёрстка."""
import json
from datetime import timedelta

import pytest

from content_engine.cli import main
from content_engine.core import publications
from content_engine.core.instance import Instance
from content_engine.core.markdown_html import convert
from content_engine.core.urls import norm_url
from content_engine.steps import verify as verify_mod
from content_engine.steps.assemble import build

from .conftest import GREENHOUSE, TODAY, set_profile
from .site_fixture import Page
from .test_brief import TID, inst  # noqa: F401 — тема «посадка чеснока» с картой спроса
from .test_schema_assemble import ready_topic

URL = "https://example.ru/blog/posadka-chesnoka-pod-zimu"


class Site:
    """Подмена сайта: адрес → страница."""

    def __init__(self):
        self.pages = {}

    def __call__(self, method, url, **kw):
        return self.pages.get(norm_url(url), Page(404, "нет"))


def built(instance):
    ready_topic(instance)
    package, _ = build(instance, TID)
    return package.main.read_text(encoding="utf-8")


def old_article_page():
    body, _ = convert(GREENHOUSE)
    schema = {"@context": "https://schema.org", "@type": "Article", "headline": "Отопление теплицы зимой"}
    return (f"<html><head><title>Отопление теплицы зимой</title>"
            f'<link rel="canonical" href="https://example.ru/blog/otoplenie-teplicy">'
            f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script></head>'
            f"<body><nav>меню</nav><article>{body}</article></body></html>")


@pytest.fixture
def site():
    return Site()


def at(project, site, day=TODAY):
    return Instance(project, today=day, transport=site)


def test_manifest_page_arrives_and_day_is_confirmation_day(inst, project, site):  # noqa: F811
    site.pages[norm_url(URL)] = Page(200, built(inst))
    next_day = at(project, site, TODAY + timedelta(days=1))
    report = verify_mod.verify(next_day, TID, URL, browser=False)
    assert report["verdict"] == "доехало", report["items"]
    assert report["publication_day"] == (TODAY + timedelta(days=1)).isoformat()
    assert report["built"] == TODAY.isoformat()
    assert any("≠ день выкладки" in n for n in report["notes"])        # на странице дата сборки
    snap = json.loads((project.parent / "data" / "snapshots" / "live" / "posadka-chesnoka-pod-zimu.json")
                      .read_text(encoding="utf-8"))
    assert snap["status"] == 200 and snap["h1"] == ["Посадка чеснока под зиму: сроки и подготовка"]
    later = at(project, site, TODAY + timedelta(days=5))
    again = verify_mod.verify(later, TID, URL, browser=False)
    assert again["publication_day"] == (TODAY + timedelta(days=1)).isoformat()   # день выкладки не переезжает
    assert len([r for r in publications.read(later) if r["event"] == "published"]) == 1


@pytest.mark.parametrize("change, item", [
    (lambda h: h.replace("<title>", "<title>Чужой "), None),
    (None, "код ответа"),
    (lambda h: h.replace('href="https://example.ru/blog/posadka-chesnoka-pod-zimu"',
                         'href="https://example.ru/blog/drugaya"'), "canonical"),
    (lambda h: h.replace("<head>", '<head><meta name="robots" content="noindex">'), "индексация"),
    (lambda h: h.split("<nav")[0] + "</body></html>", "текст"),
])
def test_not_arrived_with_reason(inst, project, site, change, item):  # noqa: F811
    html = built(inst)
    if change is None:
        site.pages[norm_url(URL)] = Page(301, "", location="https://example.ru/blog/inoe")
    else:
        site.pages[norm_url(URL)] = Page(200, change(html))
    report = verify_mod.verify(at(project, site), TID, URL, browser=False)
    assert report["verdict"] == "не доехало"
    failed = [i["check"] for i in report["items"] if not i["ok"]]
    assert (item or "title") in failed, report["items"]
    assert main(["publish", "verify", TID, "--url", URL, "--no-browser", "--profile", str(project)]) in (1, 3)


def test_old_article_by_corpus_without_description_and_h1(project, site):
    site.pages["example.ru/blog/otoplenie-teplicy"] = Page(200, old_article_page())
    inst = at(project, site)
    report = verify_mod.verify(inst, "otoplenie-teplicy", "https://example.ru/blog/otoplenie-teplicy", browser=False)
    assert report["verdict"] == "доехало", report["items"]
    assert report["reference"] == "корпус"
    assert any("description и H1 не сверялись" in n for n in report["notes"])
    assert {i["check"] for i in report["items"]}.isdisjoint({"description", "H1"})
    assert report["publication_day"] is None and report["day_source"] == "не установлен"
    fake = verify_mod.verify(inst, "otoplenie-teplicy", "https://example.ru/blog/net-takoj-stranicy",
                             browser=False)
    assert fake["verdict"] == "не доехало"


def test_day_restored_from_sitemap_lastmod(project, site, tmp_path):
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text("<urlset><url><loc>https://example.ru/blog/otoplenie-teplicy</loc>"
                       "<lastmod>2025-11-20T10:00:00+03:00</lastmod></url></urlset>", encoding="utf-8")
    set_profile(project, lambda d: d["site"].update(sitemap_file=str(sitemap)))
    site.pages["example.ru/blog/otoplenie-teplicy"] = Page(200, old_article_page())
    report = verify_mod.verify(at(project, site), "otoplenie-teplicy", None, browser=False)
    assert report["url"] == "https://example.ru/blog/otoplenie-teplicy"          # адрес — по slug из карты сайта
    assert report["publication_day"] == "2025-11-20" and report["day_source"].startswith("восстановлен")


def test_browser_missing_is_not_connected_not_passed(project, site, monkeypatch):
    from content_engine.steps import layout
    set_profile(project, lambda d: d["publish"].update(browser=True))
    monkeypatch.setattr(layout, "available", lambda: False)
    site.pages["example.ru/blog/otoplenie-teplicy"] = Page(200, old_article_page())
    report = verify_mod.verify(at(project, site), "otoplenie-teplicy", "https://example.ru/blog/otoplenie-teplicy")
    assert report["layout"]["status"] == "не подключено" and "Playwright" in report["layout"]["why"]
    assert report["verdict"] == "доехало"
    text = verify_mod.render(report)
    assert "вёрстка: не подключено" in text and "прошло" not in text.split("вёрстка:")[1].split("\n")[0]


def test_layout_in_browser_blocks_counters(project, tmp_path):
    """П32: браузером на четырёх ширинах; запрос к счётчику перехвачен до сети, снимки на месте."""
    from content_engine.steps import layout
    if not layout.available():
        pytest.skip("Playwright не установлен")
    set_profile(project, lambda d: (d["publish"].update(browser=True, widths=[1024, 360]),
                                    d["assemble"].update(required_elements=["h1"])))
    page = tmp_path / "page.html"
    page.write_text('<html><body><h1>Заголовок</h1><p>Текст</p>'
                    '<img src="https://mc.yandex.ru/watch/1" alt=""></body></html>', encoding="utf-8")
    report = layout.check(Instance(project, today=TODAY), page.as_uri(), out_dir=tmp_path / "shots")
    assert report["status"] == "прошло", report["problems"]
    assert [w["width"] for w in report["widths"]] == [1024, 360]
    assert len(list((tmp_path / "shots").glob("*.png"))) == 2
    assert report["network"] and all(n["status"] == "заблокирован" and isinstance(n["width"], int)
                                     for n in report["network"])
    assert report["counters_reached"] == 0
