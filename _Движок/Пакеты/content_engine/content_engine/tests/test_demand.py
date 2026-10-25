"""Сбор спроса (Т6): склейка, отсев, упавший источник."""
from content_engine.adapters.demand import demand_row
from content_engine.core import store
from content_engine.core.errors import AccessDenied
from content_engine.core.instance import Instance
from content_engine.core.number import ESTIMATE, FACT
from content_engine.steps.demand import collect

from .conftest import TODAY, set_profile


class Rows:
    def __init__(self, name, rows):
        self.name, self.rows = name, rows

    def fetch(self, period_from, period_to):
        return [demand_row(q, self.name, period_from, period_to, "2026-03-01", page=page, **values)
                for q, page, values in self.rows]


class Broken:
    name = "сломанный"

    def fetch(self, period_from, period_to):
        raise AccessDenied("HTTP 401 — доступ отозван")


CONSOLE = Rows("gsc", [("Отопление теплицы", "", {"impressions": (40, FACT)}),
                       ("котёл в теплице", "", {"impressions": (10, FACT)}),
                       ("котлы теплиц", "", {"impressions": (10, FACT)}),
                       ("отопления теплиц", "", {"impressions": (10, FACT)}),
                       ("садик бренд", "", {"impressions": (90, FACT)}),
                       ("редкий запрос сада", "", {"impressions": (1, FACT)}),
                       ("скачать схему теплицы", "", {"impressions": (30, FACT)})])
KEYS = Rows("keysso", [("отопление теплицы", "example.ru/blog/otoplenie-teplicy",
                        {"frequency": (700, ESTIMATE), "position": (6, ESTIMATE)})])


def run(project, sources):
    set_profile(project, lambda d: d.update(brand_words=["садик"]))
    instance = Instance(project, today=TODAY)
    return instance, collect(instance, sources)


def test_sources_glued_brand_and_junk_dropped(project):
    instance, report = run(project, [("gsc", lambda: CONSOLE), ("keysso", lambda: KEYS)])
    rows = store.read_jsonl(instance.file("demand"))
    poliv = [r for r in rows if "отоплен" in r["query"].lower()]
    kotel = [r for r in rows if "кот" in r["query"].lower()]
    assert len(kotel) == 1 and kotel[0]["impressions"]["value"] == 20
    assert len(poliv) == 1
    assert poliv[0]["impressions"]["value"] == 50 and poliv[0]["frequency"]["value"] == 700
    assert sorted(poliv[0]["sources"]) == ["gsc", "keysso"]
    assert poliv[0]["pages"][0]["url"] == "example.ru/blog/otoplenie-teplicy"
    assert report["dropped"]["бренд"] == 1 and report["dropped"]["мусор"] == 1
    assert report["dropped"]["ниже порога показов"] == 1
    assert "записано 2 — прочитано 2" in report["write"]


def test_broken_source_does_not_zero_others(project):
    _, report = run(project, [("сломанный", Broken), ("keysso", lambda: KEYS)])
    assert report["sources"]["сломанный"]["status"] == "нет данных"
    assert "401" in report["sources"]["сломанный"]["reason"]
    assert report["kept"] == 1
