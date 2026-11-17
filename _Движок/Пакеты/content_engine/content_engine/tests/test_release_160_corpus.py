"""1.6.0, `corpus import` (аудит 17.11.2026, Н17): корпус сайта с историей одной командой — индекс карт, текст из `div`,
дата со страницы, только свой сайт, чужие файлы не трогаются, строка профиля проходит проверку."""
from content_engine.cli import main
from content_engine.core.instance import Instance
from content_engine.steps import corpus_cmd

from .conftest import TODAY, set_profile
from .site_fixture import Page

INDEX = ("<?xml version='1.0'?><sitemapindex><sitemap><loc>https://example.ru/sitemap-1.xml</loc></sitemap>"
         "<sitemap><loc>https://example.ru/sitemap-2.xml</loc></sitemap>"
         "<sitemap><loc>https://other.example/sitemap.xml</loc></sitemap></sitemapindex>")
MAP1 = ("<urlset><url><loc>https://example.ru/blog/kak-vybrat-nasos</loc><lastmod>2025-05-01</lastmod></url>"
        "<url><loc>https://example.ru/blog</loc></url><url><loc>https://example.ru/catalog/nasos-1</loc></url></urlset>")
MAP2 = ("<urlset><url><loc>https://example.ru/blog/chistka-kolodca</loc><lastmod>2025-06-10</lastmod></url>"
        "<url><loc>https://example.ru/blog/udalena</loc></url></urlset>")
NASOS = ("<html><head><title>Насос — сайт</title><meta property='article:published_time' content='2025-04-20T10:00:00'>"
         "</head><body><nav>Меню сайта</nav><article><h1>Как выбрать насос</h1><div>Вступление прямо в div."
         "<h2>Мощность</h2><p>Абзац про мощность.</p><ul><li>Первый пункт</li></ul></div></article>"
         "<footer>Подвал</footer></body></html>")
KOLODEC = "<html><head><title>Чистка колодца</title></head><body><main><h2>Когда чистить</h2><p>Весной.</p></main></body></html>"


class Site:
    def __init__(self):
        self.log = []

    def __call__(self, method, url, **kw):
        self.log.append(url)
        pages = {"https://example.ru/sitemap.xml": INDEX, "https://example.ru/sitemap-1.xml": MAP1,
                 "https://example.ru/sitemap-2.xml": MAP2, "https://example.ru/blog/kak-vybrat-nasos": NASOS,
                 "https://example.ru/blog/chistka-kolodca": KOLODEC}
        return Page(200, pages[url]) if url in pages else Page(404, "нет")


def setup(project):
    set_profile(project, lambda d: d["site"].update(sitemap_url="https://example.ru/sitemap.xml"))
    site = Site()
    return Instance(project, today=TODAY, transport=site), site


def test_import_walks_sitemap_index_and_writes_markdown(project):
    instance, site = setup(project)
    out = corpus_cmd.run_import(instance, "corpus/site", None, False, transport=site)
    assert (out["urls"], out["written"], out["read"], len(out["skipped"])) == (3, 2, 2, 1)
    text = (project.parent / "corpus" / "site" / "kak-vybrat-nasos.md").read_text(encoding="utf-8")
    assert "Вступление прямо в div." in text and "## Мощность" in text and "- Первый пункт" in text
    assert "Меню сайта" not in text and "Подвал" not in text
    assert "date: '2025-04-20'" in text or "date: 2025-04-20" in text
    kolodec = (project.parent / "corpus" / "site" / "chistka-kolodca.md").read_text(encoding="utf-8")
    assert "2025-06-10" in kolodec                                  # даты на странице нет — lastmod карты


def test_import_keeps_foreign_file_and_skips_foreign_host(project):
    instance, site = setup(project)
    folder = project.parent / "corpus" / "site"
    folder.mkdir(parents=True)
    (folder / "chistka-kolodca.md").write_text("---\nslug: chistka-kolodca\n---\n\nсвой текст\n", encoding="utf-8")
    out = corpus_cmd.run_import(instance, "corpus/site", None, False, transport=site)
    assert (folder / "chistka-kolodca.md").read_text(encoding="utf-8").endswith("свой текст\n")
    assert any("чужой файл" in s for s in out["skipped"])
    assert not any("other.example" in u for u in site.log) and any("other.example" in n for n in out["notes"])


def test_import_profile_line_passes_profile_check(project):
    instance, site = setup(project)
    out = corpus_cmd.run_import(instance, "corpus/site", None, False, transport=site)
    set_profile(project, lambda d: d["corpus"]["folders"].append(out["profile_folder"]))
    assert main(["profile", "check", "--profile", str(project)]) == 0
    pages, _ = Instance(project, today=TODAY).corpus()
    assert {"kak-vybrat-nasos", "chistka-kolodca"} <= {p.slug for p in pages}


def test_import_same_slug_in_two_sections_and_repeated_sitemap(project):
    """Проверка Codex 18.11.2026: одинаковый slug в разных разделах не затирает страницу молча; повтор карты в индексе
    не тратит лимит и не читается дважды."""
    set_profile(project, lambda d: d["site"].update(sitemap_url="https://example.ru/sitemap.xml",
                                                    article_sections=["blog", "news"]))
    index = ("<sitemapindex>" + "<sitemap><loc>https://example.ru/s1.xml</loc></sitemap>" * 3 + "</sitemapindex>")
    smap = ("<urlset><url><loc>https://example.ru/blog/nasos</loc></url>"
            "<url><loc>https://example.ru/news/nasos</loc></url></urlset>")
    log = []

    def site(method, url, **kw):
        log.append(url)
        return Page(200, {"https://example.ru/sitemap.xml": index, "https://example.ru/s1.xml": smap}.get(
            url, "<html><body><article><h1>Насос</h1><p>Текст.</p></article></body></html>"))

    out = corpus_cmd.run_import(Instance(project, today=TODAY, transport=site), "corpus/site", None, False,
                                transport=site)
    assert out["written"] == out["read"] == 1 and any("тот же slug" in s for s in out["skipped"])
    assert log.count("https://example.ru/s1.xml") == 1
