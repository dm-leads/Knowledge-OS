"""Корпус (Т4.3) и слой 2 — тексты статей (Т10, Р4)."""
from content_engine.adapters.corpus import split_sections
from content_engine.core.instance import Instance
from content_engine.steps import dedup
from content_engine.steps.gate_topic import Candidate, gate

from .conftest import GREENHOUSE, SNAPSHOT, TODAY, write_article


def test_sections_by_headings():
    sections, how = split_sections(GREENHOUSE, "Содержание")
    assert how == "headings"
    assert [s.heading for s in sections] == ["", "Чем топить теплицу", "Полив теплицы зимой", "Частые вопросы"]


def test_sections_by_table_of_contents():
    sections, how = split_sections(SNAPSHOT, "Содержание")
    assert how == "toc"
    assert [s.heading for s in sections] == ["", "Когда поливать сад", "Сколько воды нужно саду"]
    assert "вечером" in sections[1].text and "ведро" in sections[2].text
    assert "Виджет" in sections[0].text


def test_one_section_when_nothing_found():
    sections, how = split_sections("Просто текст без разделов.\nВторая строка.", "Содержание")
    assert how == "none" and len(sections) == 1


def test_last_folder_wins_by_slug(project):
    root = project.parent
    write_article(root / "fresh", "ukrytie-roz", "Укрытие роз: новая версия", "## Раздел\n\nТекст.\n")

    def add_folder(data):
        folder = dict(data["corpus"]["folders"][0], path="fresh")
        data["corpus"]["folders"].append(folder)

    from .conftest import set_profile
    set_profile(project, add_folder)
    pages, report = Instance(project, today=TODAY).corpus()
    roses = [p for p in pages if p.slug == "ukrytie-roz"]
    assert len(roses) == 1 and roses[0].title == "Укрытие роз: новая версия"
    assert report["replaced"] == 1
    assert report["without_sections"] == []


def test_url_from_header_or_template(project):
    pages = {p.slug: p for p in Instance(project, today=TODAY).corpus()[0]}
    assert pages["poliv-sada"].url == "example.ru/garden/poliv-sada"
    assert pages["gazon-vesnoj"].url == "example.ru/blog/gazon-vesnoj"


def test_topic_closed_by_section_of_other_article(project):
    """Синтетическая копия провала: тему закрывает раздел статьи о другом предмете."""
    instance = Instance(project, today=TODAY)
    candidate = Candidate("полив теплицы зимой",
                          ["полив теплицы зимой", "как поливать теплицу зимой", "вода для полива теплицы"])
    report = gate(instance, candidate, offline=True, layers={2})
    layer = report["layers"][0]
    assert report["verdict"] == "пересекается" and not report["complete"]
    assert layer["section"] == "Полив теплицы зимой"
    assert layer["page"] == "example.ru/blog/otoplenie-teplicy"


def test_free_topic_passes_layer_two(project):
    instance = Instance(project, today=TODAY)
    report = gate(instance, Candidate("обрезка малины осенью", ["обрезка малины осенью", "как обрезать малину",
                                                               "когда обрезать малину"]), offline=True, layers={2})
    assert report["layers"][0]["outcome"] == dedup.PASS


def test_own_article_excluded(project):
    instance = Instance(project, today=TODAY)
    candidate = Candidate("укрытие роз на зиму", ["укрытие роз на зиму", "как укрыть розы", "чем укрыть розы"],
                          skip_slugs=frozenset({"ukrytie-roz"}))
    assert gate(instance, candidate, offline=True, layers={2})["layers"][0]["outcome"] == dedup.PASS


def test_fewer_than_three_formulations_refused(project):
    import pytest
    from content_engine.core.errors import EngineError
    with pytest.raises(EngineError, match="не меньше 3"):
        gate(Instance(project, today=TODAY), Candidate("тема", ["тема", "тема"]), offline=True, layers={2})
