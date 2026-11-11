"""1.5.4: title и description страницы — из шапки текста (как у гейта статьи); бриф не видит дублем страницу своей темы.

Находка выпуска 418 (11.11.2026): описание уточнили на вычитке в шапке текста, а сборка взяла старое из брифа — в
манифест, разметку и блок для вставки; «доехало» сверялось с манифестом и упало на верной странице. Править бриф
нельзя: правка снимает утверждение, а переутверждение видело в уже вставленной странице темы дубль самой себя.
Сборщик проекта мог остаться на брифе — поэтому сборка и контракт сверяют манифест с правилом кодом.
"""
import json

import yaml

from content_engine.assemblers.base import Package
from content_engine.cli import main
from content_engine.core import publications
from content_engine.core.instance import Instance
from content_engine.steps import assemble
from content_engine.steps import brief as brief_mod
from content_engine.steps import schema

from .conftest import TODAY, set_profile, write_article
from .test_brief import GOOD, TID, inst, put  # noqa: F401 — фикстура темы с картой спроса
from .test_gate_article import CLEAN
from .test_schema_assemble import ready_topic

NEW_DESC = ("Когда сажать озимый чеснок по температуре почвы, какой сорт выбрать и как подготовить грядку, "
            "чтобы зубки укоренились до первых морозов.")
NEW_TITLE = "Чеснок под зиму: когда сажать и как подготовить грядку"
EDITED = CLEAN.replace(GOOD["description"], NEW_DESC).replace(f"title: {GOOD['title']}", f"title: {NEW_TITLE}")

BRIEF_ADAPTER = '''from content_engine.assemblers.base import write_manifest
from content_engine.assemblers.html import HtmlAssembler


class Assembler(HtmlAssembler):
    """Сборщик проекта, который по старинке кладёт поля страницы из брифа."""
    name = "из брифа"

    def build(self, article_path, schema, images, brief):
        package = super().build(article_path, schema, images, brief)
        m = {k: v for k, v in package.manifest.items() if k not in ("files", "schema_version", "kind")}
        m.update(title=brief.get("title", ""), description=brief.get("description", ""))
        return write_manifest(self.out_dir, m, list(package.manifest["files"]))
'''


def test_page_fields_header_first_brief_fallback(inst):  # noqa: F811
    from content_engine.adapters.corpus import split_front_matter
    meta, _ = split_front_matter(EDITED)
    assert schema.page_fields(inst.p, meta, GOOD) == {"title": NEW_TITLE, "description": NEW_DESC}
    assert schema.page_fields(inst.p, {}, GOOD) == {"title": GOOD["title"], "description": GOOD["description"]}


def test_html_build_takes_fields_edited_in_text_header(inst, project):  # noqa: F811
    assert EDITED.count(NEW_DESC) == 1 and EDITED.count(NEW_TITLE) == 1 and 120 <= len(NEW_DESC) <= 160
    ready_topic(inst, EDITED)
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 0
    package = Package.load(inst.brief_dir(TID) / "package")
    assert package.manifest["description"] == NEW_DESC and package.manifest["title"] == NEW_TITLE
    data = json.loads((package.folder / "schema.json").read_text(encoding="utf-8"))
    article = next(n for n in data["@graph"] if n["@type"] == "Article")
    assert article["description"] == NEW_DESC
    page = (package.folder / "article.html").read_text(encoding="utf-8")
    assert NEW_DESC in page and GOOD["description"] not in page and f"<title>{NEW_TITLE}</title>" in page


def test_markdown_build_takes_fields_edited_in_text_header(inst, project):  # noqa: F811
    def markdown(d):                       # у markdown оглавления нет — проверка toc здесь не о том
        d["assemble"].update(adapter="markdown")
        d["assemble"]["levels"].update(toc="warn")
    set_profile(project, markdown)
    i = Instance(project, today=TODAY)
    ready_topic(i, EDITED)
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 0
    package = Package.load(i.brief_dir(TID) / "package")
    assert package.manifest["description"] == NEW_DESC and package.manifest["title"] == NEW_TITLE
    head = yaml.safe_load((package.folder / "article.md").read_text(encoding="utf-8").split("---")[1])
    assert head["description"] == NEW_DESC and head["title"] == NEW_TITLE


def test_build_and_contract_refuse_adapter_taking_fields_from_brief(inst, project):  # noqa: F811
    adapters = project.parent / "adapters"
    adapters.mkdir(exist_ok=True)
    (adapters / "from_brief.py").write_text(BRIEF_ADAPTER, encoding="utf-8")
    set_profile(project, lambda d: d["assemble"].update(adapter="module:adapters/from_brief.py:Assembler"))
    i = Instance(project, today=TODAY)
    problems = assemble.contract(i)
    assert any("не по правилу гейта статьи" in p and "description" in p for p in problems)
    ready_topic(i, EDITED)
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 1      # сборка встаёт, пакет не готов
    ready_topic(i, CLEAN)                                                       # шапка = бриф — расхождения нет
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 0


def test_brief_check_ignores_page_released_by_its_topic(inst, project):  # noqa: F811
    write_article(project.parent / "texts", GOOD["slug"], GOOD["title"], "Текст уже вставленной статьи.\n" * 20)
    i = Instance(project, today=TODAY)
    put(i, GOOD)
    dup = lambda: any("почти дублирует" in e for e in brief_mod.check(i, TID)["errors"])     # noqa: E731
    assert dup()                                          # чужая страница с тем же slug и заголовком — дубль
    package = i.brief_dir(TID) / "package"
    package.mkdir(parents=True, exist_ok=True)
    (package / "manifest.json").write_text(json.dumps({"slug": GOOD["slug"]}), encoding="utf-8")
    assert not dup()                                      # пакет собран этой темой с этим slug — страница своя
    (package / "manifest.json").write_text(json.dumps({"slug": "drugoj-slug"}), encoding="utf-8")
    assert dup()                                          # пакет темы с другим slug — исключения нет
    (package / "manifest.json").write_text("[1, 2]", encoding="utf-8")
    assert dup()                                          # битый манифест — проверка не падает, исключения нет
    publications.log(i, "published", id=TID, slug=GOOD["slug"], url="https://example.ru/blog/" + GOOD["slug"],
                     day=TODAY.isoformat())
    assert not dup()                                      # запись «доехало» темы с этим slug — страница своя
