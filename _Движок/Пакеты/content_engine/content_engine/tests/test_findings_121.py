"""Находки первой статьи и правило HTML (З36, Н1–Н5): папка темы и передача статьи в папку проекта с откатом,
кандидаты перелинковки по подзаголовкам брифа, день вставки, тема без ставки, публикуемый HTML без комментариев."""
import json
from datetime import timedelta

import pytest
import yaml

from content_engine.assemblers.base import Package, write_manifest
from content_engine.cli import main
from content_engine.core import bets, store
from content_engine.core.errors import EngineError
from content_engine.core.htmldoc import comments_left, strip_comments
from content_engine.core.instance import Instance
from content_engine.core.transfer import BuiltinTransfer, clean_name
from content_engine.core.urls import norm_url
from content_engine.steps import assemble
from content_engine.steps import brief as brief_mod
from content_engine.steps import check as check_mod
from content_engine.steps import records as records_mod
from content_engine.steps import verify as verify_mod

from .conftest import TODAY, set_profile
from .site_fixture import Page
from .test_brief import GOOD, TID, inst, put  # noqa: F401
from .test_records import released  # noqa: F401
from .test_verify import URL, Site, built


# --- Н1: папка темы из профиля --------------------------------------------------------------------------------------

def test_topic_dir_from_profile(project):
    set_profile(project, lambda d: d["paths"].update(topic_dir="work/{id}"))
    instance = Instance(project, today=TODAY)
    assert instance.brief_dir("T7") == instance.root / "work" / "T7"
    assert instance.topics_root() == instance.root / "work"
    (instance.brief_dir("T7") / "brief.yaml").write_text(yaml.safe_dump({"author": "Автор Примеров"},
                                                                        allow_unicode=True), encoding="utf-8")
    from content_engine.core import approval
    approval.write(instance, "T7", "Человек")
    assert brief_mod._month_load(instance)["Автор Примеров"] == 1


# --- Н1: передача статьи в папку проекта ----------------------------------------------------------------------------

def _published(project, allow):
    def change(d):
        d["paths"].update(published_dir="vypusk/{slug}", allow=allow)
        d["corpus"]["folders"].append({"path": "vypusk", "pattern": "*/article.md", "exclude": [],
                                       "fields": {"id": "id", "slug": "slug", "title": "title", "url": "url"},
                                       "url_template": "https://{domain}/{section}/{slug}", "section": "blog",
                                       "index_csv": ""})
    set_profile(project, change)


def test_transfer_needs_right_and_apply_then_corpus_sees_article(released, project):  # noqa: F811
    instance, day = released
    folder = instance.root / "vypusk" / "posadka-chesnoka-pod-zimu"
    _published(project, "files")
    out = records_mod.record(Instance(project, today=day), TID, apply=True)
    assert any("paths.allow = write" in line for line in out["lines"]) and not folder.exists()
    _published(project, "write")
    out = records_mod.record(Instance(project, today=day), TID, apply=False)
    assert any("план" in line and "папка проекта" in line for line in out["lines"]) and not folder.exists()
    out = records_mod.record(Instance(project, today=day), TID, apply=True)
    line = next(line for line in out["lines"] if line.startswith("папка проекта"))
    assert "записано 1 — подтверждено 1" in line and "корпус видит статью" in line, line
    assert sorted(p.name for p in folder.iterdir()) == ["article.md", "schema.json"]
    pages, _ = Instance(project, today=day).corpus()
    assert any(p.slug == "posadka-chesnoka-pod-zimu" for p in pages)
    again = records_mod.record(Instance(project, today=day), TID, apply=True)
    assert any("уже лежит" in line for line in again["lines"])
    # откат по виду снимка — в ручном режиме, без публикатора
    assert main(["publish", "rollback", TID, "--apply", "--profile", str(project)]) == 0
    assert not folder.exists()


def test_transfer_refuses_foreign_files_and_path_outside_repo(released, project):  # noqa: F811
    instance, day = released
    _published(project, "write")
    folder = instance.root / "vypusk" / "posadka-chesnoka-pod-zimu"
    folder.mkdir(parents=True)
    (folder / "Текст статьи.md").write_text("чужое", encoding="utf-8")
    out = records_mod.record(Instance(project, today=day), TID, apply=True)
    assert any("чужие файлы" in line and "не перезаписывает" in line for line in out["lines"])
    assert (folder / "Текст статьи.md").read_text(encoding="utf-8") == "чужое"
    set_profile(project, lambda d: d["paths"].update(published_dir="../../../../../../вне/{slug}"))
    with pytest.raises(EngineError, match="вне репозитория"):
        BuiltinTransfer(Instance(project, today=day)).folder(TID)


def test_clean_folder_name():
    assert clean_name("Плесень в шкафу: что делать?") == "Плесень в шкафу что делать"
    assert clean_name(' «Кто»  отвечает / за "вентиляцию". ') == "«Кто» отвечает за вентиляцию"


# --- Н2: кандидаты перелинковки по подзаголовкам брифа --------------------------------------------------------------

def test_heading_touching_foreign_key_adds_link_candidate(inst):  # noqa: F811
    data = store.read_json(inst.file("demand_map"), "demand_map")
    url = "https://example.ru/blog/ukrytie-roz-zimoy"
    data["records"].append({"id": "ukrytie-roz-zimoy", "url": url, "type": "article", "title": "Укрытие роз",
                            "corpus_id": "", "key": [], "title_key": [], "target_keys": ["укрытие роз зимой"],
                            "head_queries": [], "status": "live", "source": "тест", "cluster_id": None,
                            "value": None})
    meta = {k: v for k, v in data.items() if k not in ("records", "schema_version", "kind")}
    store.write_json(inst.file("demand_map"), "demand_map", data["records"], meta=meta)
    brief_mod.make_task(inst, TID)
    brief = json.loads(json.dumps(GOOD))
    brief["sections"][1]["h3"] = ["Укрытие роз зимой рядом с грядкой чеснока и лука"]
    put(inst, brief)
    out = brief_mod.check(inst, TID)
    assert not out["errors"], out["errors"]
    assert any(w.startswith("нужна ссылка на " + url) for w in out["warnings"])
    links = json.loads((inst.brief_dir(TID) / brief_mod.CONTEXT).read_text(encoding="utf-8"))["links"]
    assert any(c["url"] == url and c["kind"] == "по подзаголовку" for c in links)
    brief["links"] = [url]
    put(inst, brief)
    assert not any("не из кандидатов" in w for w in brief_mod.check(inst, TID)["warnings"])


# --- Н3: день вставки ------------------------------------------------------------------------------------------------

def test_inserted_on_is_publication_day_with_source(inst, project):  # noqa: F811
    site = Site()
    site.pages[norm_url(URL)] = Page(200, built(inst))
    later = Instance(project, today=TODAY + timedelta(days=3), transport=site)
    with pytest.raises(EngineError, match="раньше дня сборки"):
        verify_mod.verify(later, TID, URL, browser=False, inserted_on=(TODAY - timedelta(days=1)).isoformat(),
                          who="Человек")
    with pytest.raises(EngineError, match="позже сегодняшнего"):
        verify_mod.verify(later, TID, URL, browser=False, inserted_on=(TODAY + timedelta(days=5)).isoformat(),
                          who="Человек")
    report = verify_mod.verify(later, TID, URL, browser=False, inserted_on=(TODAY + timedelta(days=1)).isoformat(),
                               who="Человек")
    assert report["publication_day"] == (TODAY + timedelta(days=1)).isoformat()
    assert "со слов: Человек" in report["day_source"]
    assert main(["publish", "verify", TID, "--url", URL, "--inserted-on", TODAY.isoformat(), "--no-browser",
                 "--profile", str(project)]) == 3                     # без --who — ошибка входа


def test_stale_package_warned_by_check(inst, project):  # noqa: F811
    built(inst)
    item = check_mod._stale_packages(Instance(project, today=TODAY + timedelta(days=2)))
    assert item["light"] != "green" and TID in item["detail"]
    assert "зелёный" not in item["detail"]


# --- Н4: тема без ставки ---------------------------------------------------------------------------------------------

def test_missing_bet_warned_and_marked(inst):  # noqa: F811
    out = brief_mod.make_task(inst, TID)
    assert any("ставки нет" in note for note in out["notes"])
    topic = {"id": TID, "topic": "тема", "bet": {}}
    added, text = bets.add(inst, topic, URL, TODAY.isoformat())
    assert added and "ставки нет" in text
    assert bets.load(inst)[0]["bet_missing"] is True


# --- Н5: публикуемый HTML без комментариев -------------------------------------------------------------------------

def test_strip_comments_keeps_scripts():
    html = ('<!--\n шапка блока \n--><p>Лид</p><!-- ЛИД --><!-- ============ H2-1 ============ -->'
            '<script>var a = "<!-- не комментарий -->";</script><style>/* <!-- */</style><!--[if IE]><p>x</p><![endif]-->')
    clean, removed = strip_comments(html)
    assert "ЛИД" not in clean and "шапка блока" not in clean and "[if IE]" not in clean
    assert '"<!-- не комментарий -->"' in clean and "<style>/* <!-- */</style>" in clean
    assert removed[:3] == ["шапка блока", "ЛИД", "============ H2-1 ============"]
    assert comments_left(clean) == 0


def test_build_strips_comments_into_notes_and_check_catches_left(inst, project):  # noqa: F811
    from .test_schema_assemble import ready_topic
    ready_topic(inst)
    adapters = project.parent / "adapters"
    adapters.mkdir()
    (adapters / "commented.py").write_text(
        "from content_engine.assemblers.html import HtmlAssembler\n\n"
        "class Assembler(HtmlAssembler):\n"
        "    def build(self, article, schema, images, brief):\n"
        "        package = super().build(article, schema, images, brief)\n"
        "        path = package.main\n"
        "        path.write_text('<!-- ЛИД -->' + path.read_text(encoding='utf-8'), encoding='utf-8')\n"
        "        return package\n", encoding="utf-8")
    set_profile(project, lambda d: d["assemble"].update(adapter="module:adapters/commented.py:Assembler"))
    instance = Instance(project, today=TODAY)
    package, results = assemble.build(instance, TID)
    assert "<!--" not in package.main.read_text(encoding="utf-8")
    assert {"file": package.manifest["main"], "comment": "ЛИД"} in package.manifest["notes"]
    assert next(r for r in results if r["check"] == "comments")["ok"]
    path = package.main
    path.write_text("<!-- забытый -->" + path.read_text(encoding="utf-8"), encoding="utf-8")
    meta = {k: v for k, v in package.manifest.items() if k not in ("files", "schema_version", "kind")}
    again = write_manifest(package.folder, meta, list(package.manifest["files"]))
    check = next(r for r in assemble.checks(instance, Package.load(again.folder)) if r["check"] == "comments")
    assert not check["ok"] and check["level"] == "block"
