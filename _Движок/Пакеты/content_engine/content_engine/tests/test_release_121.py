"""Выпуск 1.2.1 — находки первой статьи у первого потребителя (сборка конструктора, разметка, разбор топа)."""
import json
import math
from pathlib import Path

from content_engine.assemblers.base import write_manifest
from content_engine.core.htmldoc import HtmlDoc
from content_engine.core.instance import Instance
from content_engine.steps import assemble, prepare, schema

from .conftest import TODAY, set_profile
from .site_fixture import Page
from .test_prepare_top import FORMULATIONS, TID, Web, page, taken_topic

# Вёрстка конструктора: id — на обёртке раздела, а не на H2; оглавление — список после строки «Содержание»;
# «Выводы» и FAQ в оглавление не входят; окно консультации — нативный якорь страницы; H1 — заголовок страницы.
BUILDER = """<html><body><div class="builder-article">
<p>Лид статьи.</p>
<div><p class="textable">Содержание</p><div><ul>
<li><a href="#pervyj">Первый раздел</a></li><li><a href="#vtoroj">Второй раздел</a></li></ul></div></div>
<div id="pervyj"><h2>Первый раздел</h2></div><p>Текст первого раздела.</p>
<div id="vtoroj"><h2>Второй раздел</h2></div><p>Текст второго. <a href="#modal-engineer">Консультация</a></p>
<div><h2>Выводы</h2></div><p>Итог.</p>
<div><h2>Частые вопросы о проверке</h2></div><p>Вопрос?</p>
</div></body></html>"""


def builder_package(tmp_path: Path, html: str, **extra):
    folder = tmp_path / "package"
    folder.mkdir()
    (folder / "article.html").write_text(html, encoding="utf-8")
    meta = {"id": "T1", "built": "2026-03-02", "title": "Т", "description": "", "slug": "proverka", "h1": "Т",
            "canonical": "https://example.ru/blog/proverka", "main": "article.html", "blocks": ["article.html"]}
    meta.update(extra)
    return write_manifest(folder, meta, ["article.html"])


def by_check(results):
    return {r["check"]: r for r in results}


def test_toc_after_marker_with_ids_on_wrappers(project, tmp_path):
    set_profile(project, lambda d: d["assemble"].update(required_elements=[".builder-article", "h1"]))
    inst = Instance(project, today=TODAY)
    package = builder_package(tmp_path, BUILDER, native_anchors=["modal-engineer"], outside_blocks=["h1"],
                              toc_skip=["Выводы"])
    got = by_check(assemble.checks(inst, package))
    assert got["toc"]["ok"], got["toc"]["detail"]
    assert got["anchors"]["ok"], got["anchors"]["detail"]          # нативный якорь объявлен сборщиком проекта
    assert got["required"]["ok"], got["required"]["detail"]        # H1 — заголовок страницы, вне блоков


def test_toc_mismatch_and_native_anchor_without_declaration_still_caught(project, tmp_path):
    inst = Instance(project, today=TODAY)
    broken = BUILDER.replace('<li><a href="#vtoroj">Второй раздел</a></li>', "")
    got = by_check(assemble.checks(inst, builder_package(tmp_path, broken)))
    assert not got["toc"]["ok"] and "Второй раздел" in got["toc"]["detail"]
    assert not got["anchors"]["ok"] and "modal-engineer" in got["anchors"]["detail"]


def test_schema_canonical_in_punycode_and_cover_from_article(project):
    set_profile(project, lambda d: d["site"].update(domains=["пример.рус"]))
    inst = Instance(project, today=TODAY)
    text = "---\nimage: https://cdn.example/cover.jpg\n---\n\n# Заголовок\n\nТекст.\n"
    data = schema.build(text, {"slug": "stranica", "description": "", "author": "Автор"}, inst.p, "2026-03-02")
    art = data["@graph"][0]
    assert art["mainEntityOfPage"] == "https://xn--e1afmkfd.xn--p1acf/blog/stranica"
    assert art["image"] == "https://cdn.example/cover.jpg"
    assert all("xn--" in c["item"] for c in data["@graph"][-1]["itemListElement"])


# --- разбор топа

class Redirecting(Web):
    """Сайт отвечает 301 на адрес без слеша и отдаёт страницу по адресу со слешем."""

    def __call__(self, method, url, **kwargs):
        if url.rstrip("/") == "https://c1.example/roses" and not url.endswith("/"):
            self.log.append(url)
            return Page(301, "", location="https://www.c1.example/roses/")
        if url == "https://www.c1.example/roses/":
            self.log.append(url)
            return Page(200, self.pages["c1.example/roses"][0])
        return super().__call__(method, url, **kwargs)


def test_same_site_redirect_is_followed_foreign_is_not(project):
    from .test_prepare_top import URLS
    web = Redirecting(dict(URLS))
    inst = taken_topic(project, web)
    out = prepare.top(inst, TID)
    assert "c1.example/roses" in [p["url"] for p in out["pages"]]


def test_almost_empty_page_not_in_numbers(project):
    from .test_prepare_top import C1, C2, URLS
    tiny = page("Р", [("С", "Да")])                    # как в жизни: десятки знаков против тысяч
    web = Web({**URLS, "c3.example/roses": tiny})
    inst = taken_topic(project, web)
    out = prepare.top(inst, TID)
    assert "c3.example/roses" not in [p["url"] for p in out["pages"]]
    assert any(s["url"] == "c3.example/roses" and "не разобрано" in s["why"] for s in out["skipped"])
    assert out["numbers"]["volume_chars"]["value"] == round((C1[1] + C2[1]) / 2)


def test_images_counted_in_article_body_without_icons_and_cover():
    html = ('<html><body><main><img src="/logo.svg"><img src="/cover.jpg" alt="обложка"><h1>Розы</h1>'
            '<div class="content"><h2>Раз</h2><p>Текст.</p><img src="/a.jpg" width="800"><h2>Два</h2>'
            '<img src="/b.jpg"><img src="/icon.png" width="24" height="24"><img src="data:image/png;base64,AA"></div>'
            '<img src="/banner.jpg"></main></body></html>')
    assert HtmlDoc(html).markers()["images"] == 2


def test_service_lines_are_not_gaps(project):
    from .test_prepare_top import URLS
    noisy = page("Розы", [("Содержание:", "раз два"), ("Обновлен: 15.10.2026", "три"),
                          ("Когда снимать укрытие весной", "Постепенно, без спешки и по погоде на неделю вперёд.")])
    web = Web({**URLS, "c3.example/roses": noisy})
    inst = taken_topic(project, web)
    gaps = {g["heading"] for g in prepare.top(inst, TID)["gaps"]}
    assert "Содержание:" not in gaps and "Обновлен: 15.10.2026" not in gaps
