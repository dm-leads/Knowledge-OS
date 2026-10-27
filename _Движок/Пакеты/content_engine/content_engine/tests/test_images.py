"""Картинки (Т30, П45): места по разбору топа, шаблон и библиотека за 0 ₽, платный провайдер только через
бюджет-гейт, отказ — заглушка, alt и кредиты в гейте, загрузка с правом и откатом, вставка в текст, добор с откатом
прогона."""
import json

import pytest

from content_engine.cli import main
from content_engine.core import spend, store
from content_engine.core.errors import EngineError
from content_engine.core.instance import Instance
from content_engine.images.base import usd_to_rub
from content_engine.steps import images as images_mod
from content_engine.steps.gate_article import check_topic

from .conftest import TODAY, set_profile
from .site_fixture import Page
from .test_brief import TID, inst  # noqa: F401
from .test_gate_article import CLEAN
from .test_schema_assemble import ready_topic

PAID = '''
from pathlib import Path


class Provider:
    def __init__(self, instance):
        self.root = Path(instance.root)

    def _count(self):
        path = self.root / "calls.txt"
        n = int(path.read_text()) if path.is_file() else 0
        path.write_text(str(n + 1))

    def price(self, request):
        return 2.0

    def make(self, request, path):
        self._count()
        if request.heading.startswith("Как"):
            raise RuntimeError("генератор не ответил")
        target = Path(path).with_suffix(".png")
        target.write_bytes(b"png")
        return {"file": str(target), "url": "", "price": 2.0}
'''
UPLOADER = '''
import json
from pathlib import Path

from content_engine.core.actions import Action


class Uploader:
    def __init__(self, instance):
        self.store = Path(instance.root) / "bucket.json"

    def _data(self):
        return json.loads(self.store.read_text()) if self.store.is_file() else {}

    def target(self, file, tid):
        return "https://cdn.example.ru/" + Path(file).name

    def snapshot(self, url):
        return {"exists": url in self._data(), "content": self._data().get(url), "read_from": url}

    def upload(self, file, url):
        if "broken" in url:
            return None
        data = self._data()
        data[url] = Path(file).stat().st_size
        self.store.write_text(json.dumps(data))
        return url

    def read_back(self, url):
        return (url in self._data(), "адрес отвечает" if url in self._data() else "адрес не отвечает")

    def restore(self, snap):
        url = snap["target"]

        def do():
            data = self._data()
            data.pop(url, None)
            self.store.write_text(json.dumps(data))
            return url

        return Action("image_upload_rollback", url, "DELETE", "снять картинку", "read_back", ("images", "write"),
                      do=do, verify=lambda _: (url not in self._data(), "снята"),
                      snapshot=lambda: {"exists": True, "content": None, "read_from": url})
'''


def topic_with_images(inst, project, n=2, **providers):  # noqa: F811
    ready_topic(inst)
    store.write_json(inst.brief_dir(TID) / "top.json", "topic_top", [],
                     meta={"serp_as_of": TODAY.isoformat(), "complete": True,
                           "numbers": {"images": {"value": n}}, "gaps": [], "facts": {}})
    set_profile(project, lambda d: d["images"]["providers"].update(**providers))
    return Instance(project, today=TODAY)


def test_template_cover_and_scheme_free_without_budget_gate(inst, project):  # noqa: F811
    instance = topic_with_images(inst, project, cover="template", scheme="template", illustration="template")
    out = images_mod.make_plan(instance, TID)
    assert "в теле 2 картинок из цели 2" in out["line"]
    made = images_mod.make(instance, TID)                       # потолки 0 — бесплатное всё равно делается
    items = made["plan"]["images"]
    assert all(i["status"] == "готово" and i["file"].endswith(".svg") for i in items)
    assert "платных 0" in made["line"] and not any(r["kind"] == "rub" for r in spend.rows(instance))


def test_library_photo_by_section_once(inst, project, tmp_path):  # noqa: F811
    (project.parent / "lib").mkdir()
    (project.parent / "lib" / "grjadka.jpg").write_bytes(b"jpg")
    (project.parent / "lib" / "index.csv").write_text(
        "file_or_url;alt;keywords\ngrjadka.jpg;Грядка под компостом;грядка компост\n"
        "https://cdn.example.ru/sroki.jpg;Календарь посадки;сроки посадки чеснок когда\n", encoding="utf-8")
    set_profile(project, lambda d: d["images"]["library"].update(index="lib/index.csv"))
    instance = topic_with_images(inst, project, cover="none", product="library", scheme="template",
                                 illustration="none")
    instance = Instance(project, today=TODAY)
    plan = images_mod.make_plan(instance, TID)["plan"]["images"]
    kinds = {i["place"]: i for i in plan}
    assert any(i["kind"] == "product" for i in plan)
    refs = [i["ref"] for i in plan if i["kind"] == "product"]
    assert len(refs) == len(set(refs))                            # одно фото — не больше раза
    images_mod.make(instance, TID)
    done = images_mod.load_plan(instance, TID)["images"]
    assert all(i["status"] in ("готово",) or i["status"].startswith("пропущено") for i in done), kinds


def test_paid_provider_needs_yes_and_failure_is_stub(inst, project):  # noqa: F811
    (project.parent / "adapters").mkdir()
    (project.parent / "adapters" / "gen.py").write_text(PAID, encoding="utf-8")
    instance = topic_with_images(inst, project, cover="none", illustration="module:adapters/gen.py:Provider",
                                 scheme="module:adapters/gen.py:Provider")
    images_mod.make_plan(instance, TID)
    assert main(["images", "make", TID, "--profile", str(project)]) == 2            # потолок 0 — отказ
    assert not (instance.root / "calls.txt").exists()
    set_profile(project, lambda d: d["budget"].update(run_limit_rub=50, month_limit_rub=500))
    assert main(["images", "make", TID, "--profile", str(project)]) == 2            # без «да» — ноль вызовов
    assert not (instance.root / "calls.txt").exists()
    assert main(["images", "make", TID, "--unattended", "--confirm-spend", "10", "--profile", str(project)]) == 2
    assert main(["images", "make", TID, "--confirm-spend", "4", "--profile", str(project)]) == 0
    plan = images_mod.load_plan(Instance(project, today=TODAY), TID)["images"]
    assert any(i["status"].startswith("отказ") for i in plan) and any(i["status"] == "готово" for i in plan)
    rub = [r for r in spend.rows(instance) if r["kind"] == "rub"]
    assert rub and rub[0]["value"] == 4.0 and rub[0]["status"] == "оценка"


def test_usd_price_needs_rate(project):
    instance = Instance(project, today=TODAY)
    with pytest.raises(EngineError, match="usd_rub"):
        usd_to_rub(instance, 0.02)
    set_profile(project, lambda d: d["budget"].update(usd_rub=95.5, usd_rub_source="ЦБ РФ, 27.10.2026"))
    assert usd_to_rub(Instance(project, today=TODAY), 0.02) == 1.91


def test_alt_and_photo_credits_by_level(inst, project):  # noqa: F811
    ready_topic(inst)
    article = inst.brief_dir(TID) / "article.md"
    article.write_text(CLEAN.replace("## Когда сажать", "![](https://cdn.example.ru/a.png)\n\n## Когда сажать"),
                       encoding="utf-8")
    alive = {"link_checker": lambda url, timeout: (True, "200")}
    report = check_topic(inst, TID, **alive)
    assert any(r["check"] == "image_alt" for r in report["reasons"])
    article.write_text(CLEAN, encoding="utf-8")
    (inst.brief_dir(TID) / "images.json").write_text(json.dumps({"images": [
        {"n": 1, "kind": "photo", "place": "Когда сажать", "author": "", "license": ""}]}), encoding="utf-8")
    report = check_topic(inst, TID, **alive)
    assert any(r["check"] == "image_credits" for r in report["reasons"])
    set_profile(project, lambda d: d["gate_article"]["levels"].update(image_credits="warn"))
    assert check_topic(Instance(project, today=TODAY), TID, **alive)["verdict"] == "пропуск"


def test_upload_needs_right_rollback_in_manual_mode_and_insert(inst, project):  # noqa: F811
    (project.parent / "adapters").mkdir()
    (project.parent / "adapters" / "up.py").write_text(UPLOADER, encoding="utf-8")
    instance = topic_with_images(inst, project, cover="template", scheme="template", illustration="template")
    set_profile(project, lambda d: d["images"].update(upload="module:adapters/up.py:Uploader"))
    instance = Instance(project, today=TODAY)
    images_mod.make_plan(instance, TID)
    images_mod.make(instance, TID)
    out = images_mod.upload(instance, TID, apply=True)
    assert "images.allow = write" in out["line"] and not (instance.root / "bucket.json").exists()
    set_profile(project, lambda d: d["images"].update(allow="write"))
    assert main(["images", "upload", TID, "--apply", "--profile", str(project)]) == 0
    bucket = json.loads((instance.root / "bucket.json").read_text())
    assert len(bucket) == 3
    plan = images_mod.load_plan(Instance(project, today=TODAY), TID)["images"]
    assert all(i["url"].startswith("https://cdn.example.ru/") for i in plan)
    assert main(["images", "insert", TID, "--profile", str(project)]) == 0
    text = (instance.brief_dir(TID) / "article.md").read_text(encoding="utf-8")
    assert "image: https://cdn.example.ru/" in text and text.count("![") == 2
    assert main(["publish", "rollback", TID, "--apply", "--profile", str(project)]) == 0
    assert json.loads((instance.root / "bucket.json").read_text()) == {}


def test_upload_not_answering_is_unconfirmed(inst, project):  # noqa: F811
    (project.parent / "adapters").mkdir()
    (project.parent / "adapters" / "up.py").write_text(UPLOADER.replace('"https://cdn.example.ru/"',
                                                                        '"https://cdn.example.ru/broken-"'),
                                                       encoding="utf-8")
    instance = topic_with_images(inst, project, cover="template", scheme="none", illustration="none", n=0)
    set_profile(project, lambda d: d["images"].update(upload="module:adapters/up.py:Uploader", allow="write"))
    instance = Instance(project, today=TODAY)
    images_mod.make_plan(instance, TID)
    images_mod.make(instance, TID)
    assert main(["images", "upload", TID, "--apply", "--profile", str(project)]) == 3


def test_backfill_queues_pages_without_images_and_run_rolls_back(project):
    class Site:
        def __call__(self, method, url, **kw):
            body = "<html><body><article><h2>Раз</h2><p>текст</p><h2>Два</h2><p>текст</p></article></body></html>"
            if "ukrytie-roz" in url:
                body = body.replace("<p>текст</p>", '<p>текст</p><img src="/a.jpg" width="800">', 1)
            return Page(200, body)
    instance = Instance(project, today=TODAY, transport=Site())
    out = images_mod.backfill(instance, apply=True)
    run_id = out["run"]
    queued = list((instance.root / "data" / "backfill" / run_id).glob("*.json"))
    slugs = sorted(p.stem for p in queued)
    assert "ukrytie-roz" not in slugs and "otoplenie-teplicy" in slugs
    snaps = list((instance.root / "data" / "snapshots" / "before" / "backfill" / run_id).glob("*.json"))
    assert len(snaps) == len(queued)
    assert main(["publish", "rollback", "--run", run_id, "--apply", "--profile", str(project)]) == 0
    assert not list((instance.root / "data" / "backfill" / run_id).glob("*.json"))
