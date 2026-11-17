"""1.6.0, гейт темы (аудит 17.11.2026, инцидент «тема свободна при живой статье-близнеце»): формулировки темы — по
основам головы кластера, а не по весу; восьмой слой — замечание о редкой общей основе в заголовке, вердикт не меняет."""
from types import SimpleNamespace

from content_engine.core.instance import Instance
from content_engine.core.normalize import Normalizer
from content_engine.steps import dedup
from content_engine.steps import queue as queue_mod
from content_engine.steps.gate_topic import Candidate

from .conftest import TODAY

NORM = Normalizer()
FILLER = ["Как выбрать кондиционер", "Уход за газоном весной", "Обрезка малины осенью", "Посадка чеснока",
          "Полив сада летом", "Отопление теплицы зимой", "Укрытие роз", "Удобрения для огурцов", "Выбор насоса",
          "Чистка колодца", "Мульча для клубники", "Пересадка яблони", "Компост своими руками", "Борьба с тлёй",
          "Капельный полив", "Хранение картофеля", "Защита от кротов", "Обработка винограда", "Посев моркови"]


def ctx_with(titles):
    items = [SimpleNamespace(page=SimpleNamespace(url=f"example.ru/blog/p{i}", title=t, slug=f"p{i}"),
                             title_key=NORM.key(t)) for i, t in enumerate(titles)]
    return SimpleNamespace(norm=NORM, index=SimpleNamespace(pages=items), pages=lambda: {})


def cand(topic):
    return Candidate(topic=topic, formulations=[topic, topic + " 2", topic + " 3"])


def test_rare_stem_shared_with_twin_title_is_note():
    ctx = ctx_with(FILLER + ["Допустимый уровень шума в квартире"])
    r = dedup.layer_rare_title(ctx, cand("уровень шума в квартире ночью"), set())
    assert r.outcome == dedup.NOTE and "Допустимый уровень шума" in r.reason


def test_single_shared_stem_is_not_note():
    ctx = ctx_with(FILLER)
    r = dedup.layer_rare_title(ctx, cand("полив газона осенью"), set())   # каждая основа — в своём заголовке
    assert r.outcome == dedup.PASS


def test_common_stem_is_not_rare():
    titles = FILLER + [f"Кондиционер для дома, модель {n}" for n in range(6)]
    r = dedup.layer_rare_title(ctx_with(titles), cand("кондиционер для дома выбор"), set())
    assert r.outcome == dedup.PASS


def test_note_does_not_change_verdict(project, monkeypatch):
    from content_engine.steps import gate_topic
    monkeypatch.setitem(dedup.LAYERS, 8, lambda ctx, c, skip: dedup.LayerResult(8, dedup.NOTE, "проверка"))
    report = gate_topic.gate(Instance(project, today=TODAY), cand("посадка чеснока под зиму"), offline=True)
    assert report["verdict"] in (gate_topic.FREE, gate_topic.OVERLAP, gate_topic.COMMERCIAL)
    monkeypatch.setitem(dedup.LAYERS, 8, lambda ctx, c, skip: dedup.LayerResult(8, dedup.PASS, "проверка"))
    assert gate_topic.gate(Instance(project, today=TODAY), cand("посадка чеснока под зиму"),
                           offline=True)["verdict"] == report["verdict"]


def test_formulations_start_from_head_and_its_stems(project):
    instance = Instance(project, today=TODAY)
    c = {"head": "нормы шума в квартире",
         "queries": ["шумоизоляция стен", "звукоизоляция потолка", "тихие соседи", "нормы шума в квартире ночью",
                     "нормы шума в квартире"]}
    out = queue_mod._formulations(instance, c, [])
    assert out[0] == "нормы шума в квартире" and out[1] == "нормы шума в квартире ночью"
