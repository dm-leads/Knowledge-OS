"""Разметка из текста, проверка JSON-LD и микроразметки, сборка и её автопроверки, контракт сборщика (Т20, П28)."""
import json

import pytest

from content_engine.assemblers.base import Package, check_manifest
from content_engine.assemblers.html import HtmlAssembler
from content_engine.cli import main
from content_engine.core.instance import Instance
from content_engine.steps import assemble, schema
from content_engine.steps import brief as brief_mod
from content_engine.steps.gate_article import PASS_, check_article, check_topic

from .conftest import TODAY, set_profile
from .test_brief import GOOD, TID, inst, put  # noqa: F401 — фикстура темы с картой спроса
from .test_gate_article import CLEAN


def ready_topic(instance, article=CLEAN):
    brief_mod.make_task(instance, TID)
    put(instance, GOOD)
    brief_mod.approve(instance, TID, "Человек")
    (instance.brief_dir(TID) / "article.md").write_text(article, encoding="utf-8")
    report = check_topic(instance, TID, link_checker=lambda url, timeout: (True, "200"))   # ссылки «живые»
    assert report["verdict"] == PASS_, report["reasons"]


def test_schema_build_from_text_and_brief(inst):  # noqa: F811
    data = schema.build(CLEAN, GOOD, inst.p, "2026-03-02")
    nodes = {n["@type"]: n for n in data["@graph"]}
    assert nodes["Article"]["headline"] == "Посадка чеснока под зиму: сроки и подготовка"
    assert nodes["Article"]["author"]["name"] == "Агроном Второй"
    assert nodes["Article"]["datePublished"] == "2026-03-02"
    assert [q["name"] for q in nodes["FAQPage"]["mainEntity"]] == [
        "Можно ли сажать чеснок из магазина?", "Нужно ли замачивать зубки?", "Чем укрыть посадки?"]
    assert nodes["BreadcrumbList"]["itemListElement"][-1]["item"] == "https://example.ru/blog/posadka-chesnoka-pod-zimu"
    assert data["slug"] == "posadka-chesnoka-pod-zimu"


@pytest.mark.parametrize("fmt", ["json-ld", "microdata"])
def test_schema_check_reads_html_and_catches_headline(inst, tmp_path, fmt):  # noqa: F811
    inst.profile["assemble"]["schema_format"] = fmt
    article = tmp_path / "article.md"
    article.write_text(CLEAN, encoding="utf-8")
    data = schema.build(CLEAN, GOOD, inst.p, "2026-03-02")
    package = HtmlAssembler(inst, tmp_path / "pkg").build(article, data, [], dict(GOOD, id=TID))
    page = package.main
    report = check_article(inst, article, html_path=page, offline=True)
    check = next(c for c in report["checks"] if c["check"] == "schema")
    assert check["ok"], check["detail"]
    assert ("микроразметка" in check["detail"]) == (fmt == "microdata")
    broken = page.read_text(encoding="utf-8").replace("Посадка чеснока под зиму: сроки и подготовка",
                                                      "Совсем другой заголовок")
    bad = tmp_path / "bad.html"
    bad.write_text(broken.replace("<h1", "<h2").replace("</h1>", "</h2>"), encoding="utf-8")
    report = check_article(inst, article, html_path=bad, offline=True)
    check = next(c for c in report["checks"] if c["check"] == "schema")
    assert not check["ok"] and "headline" in check["detail"]


def test_json_schema_mismatch_caught(inst, tmp_path):  # noqa: F811
    article = tmp_path / "article.md"
    article.write_text(CLEAN, encoding="utf-8")
    data = schema.build(CLEAN, GOOD, inst.p, "2026-03-02")
    data["@graph"][0]["headline"] = "Совсем другой заголовок"
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    report = check_article(inst, article, schema_path=path, offline=True)
    assert report["verdict"] == "черновик" and any(r["check"] == "schema" for r in report["reasons"])


def test_build_manifest_has_sums_of_all_files(inst, project):  # noqa: F811
    ready_topic(inst)
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 0
    package = Package.load(inst.brief_dir(TID) / "package")
    files = sorted(p.name for p in package.folder.iterdir() if p.name != "manifest.json")
    assert sorted(package.manifest["files"]) == files == ["article.html", "schema.json", "text.txt"]
    assert check_manifest(package.folder, package.manifest) == []
    m = package.manifest
    assert m["slug"] == GOOD["slug"] and m["h1"] == GOOD["h1"] and m["built"] == TODAY.isoformat() and m["ready"]
    assert m["canonical"] == "https://example.ru/blog/posadka-chesnoka-pod-zimu"
    (package.folder / "text.txt").write_text("подменили", encoding="utf-8")
    assert any("сумма text.txt" in p for p in check_manifest(package.folder, package.manifest))


def test_build_refused_when_text_changed_after_gate(inst, project):  # noqa: F811
    ready_topic(inst)
    (inst.brief_dir(TID) / "article.md").write_text(CLEAN + "\nДописали после гейта.\n", encoding="utf-8")
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 1


BROKEN = CLEAN.replace("## Частые вопросы", "Подробнее — в [разделе о поливе](#poliv).\n\n## Частые вопросы")


def test_broken_anchor_and_long_section_by_level(inst, project):  # noqa: F811
    long_text = " ".join(["Грядку рыхлят, поливают и мульчируют перегноем слоем."] * 120)
    article = BROKEN.replace("Свежий навоз не годится", long_text + " Свежий навоз не годится")
    ready_topic(inst, article)
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 1     # anchors: block
    m = json.loads((inst.brief_dir(TID) / "package" / "manifest.json").read_text(encoding="utf-8"))
    by = {c["check"]: c for c in m["checks"]}
    assert not by["anchors"]["ok"] and "poliv" in by["anchors"]["detail"] and m["ready"] is False
    assert not by["section_borders"]["ok"] and "Как подготовить грядку" in by["section_borders"]["detail"]
    set_profile(project, lambda d: d["assemble"]["levels"].update(anchors="warn"))
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 0     # section_borders: warn


def test_contract_builtin_and_instance_adapters(project, tmp_path):
    instance = Instance(project, today=TODAY)
    assert assemble.contract(instance) == []
    set_profile(project, lambda d: d["assemble"].update(adapter="markdown"))
    assert assemble.contract(Instance(project, today=TODAY)) == []
    adapters = project.parent / "adapters"
    adapters.mkdir()
    (adapters / "good.py").write_text(
        "from content_engine.assemblers.html import HtmlAssembler\n\n"
        "class Assembler(HtmlAssembler):\n    name = 'проект'\n", encoding="utf-8")
    (adapters / "bad.py").write_text(
        "import json\nfrom pathlib import Path\nfrom content_engine.assemblers.base import Package\n\n"
        "class Assembler:\n    name = 'плохой'\n\n    def __init__(self, instance, out_dir):\n"
        "        self.out = Path(out_dir)\n\n    def build(self, article, schema, images, brief):\n"
        "        self.out.mkdir(parents=True)\n        (self.out / 'page.html').write_text('x')\n"
        "        m = {'files': {'page.html': '0'}, 'main': 'page.html', 'blocks': ['page.html']}\n"
        "        (self.out / 'manifest.json').write_text(json.dumps(m))\n        return Package(self.out, m)\n",
        encoding="utf-8")
    set_profile(project, lambda d: d["assemble"].update(adapter="module:adapters/good.py:Assembler"))
    assert main(["assemble", "contract", "--profile", str(project)]) == 0
    set_profile(project, lambda d: d["assemble"].update(adapter="module:adapters/bad.py:Assembler"))
    problems = assemble.contract(Instance(project, today=TODAY))
    assert any("сумма page.html" in p for p in problems) and any("поля «title»" in p for p in problems)
    assert main(["assemble", "contract", "--profile", str(project)]) == 1


def test_build_without_approval_refused(inst, project):  # noqa: F811
    brief_mod.make_task(inst, TID)
    put(inst, GOOD)
    (inst.brief_dir(TID) / "article.md").write_text(CLEAN, encoding="utf-8")
    assert main(["assemble", "build", TID, "--profile", str(project)]) == 1
