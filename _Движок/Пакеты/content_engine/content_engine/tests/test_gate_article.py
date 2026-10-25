"""Гейт статьи без модели (Т13): чистая статья — «пропуск», с подложенными ошибками — «черновик» с причинами."""
from content_engine.core.instance import Instance
from content_engine.steps.demand_map import build_map
from content_engine.steps.gate_article import DRAFT, PASS_, check_article

from .conftest import TODAY
from .test_demand_map import setup

CLEAN = """---
title: Посадка чеснока под зиму: сроки, сорта и подготовка грядки
description: Когда сажать озимый чеснок, как выбрать сорт и подготовить грядку, чтобы зубки укоренились до морозов и дали крупные головки следующим летом.
slug: posadka-chesnoka-pod-zimu
---

# Посадка чеснока под зиму: сроки и подготовка

Озимый чеснок сажают осенью, за три-четыре недели до устойчивых заморозков. Зубок успевает укорениться, но не
прорастает. Сроки зависят от региона: в средней полосе это конец сентября и октябрь, южнее — ноябрь.

## Когда сажать

Ориентир — температура почвы около десяти градусов на глубине посадки. Точные сроки по регионам приводит
[справочник агронома](https://agro.example/chesnok) и [календарь садовода](https://calendar.example/oktyabr).

## Как подготовить грядку

Грядку перекапывают за две недели до посадки и вносят перепревший компост. Свежий навоз не годится: головки
растут рыхлыми. Для мульчи подойдёт [укрывной материал из каталога](https://example.ru/catalog/mulcha).

## Частые вопросы

**Можно ли сажать чеснок из магазина?**
Лучше брать районированный посадочный материал, магазинный часто не зимует.

Нужно ли замачивать зубки?
Достаточно перебрать и выбросить мягкие и с пятнами.

Чем укрыть посадки?
Слоем мульчи в пять сантиметров после первых заморозков.
"""


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def make(project):
    instance = setup(project)
    build_map(instance, offline=True)
    return Instance(project, today=TODAY)


def test_clean_article_passes(project, tmp_path):
    report = check_article(make(project), write(tmp_path, "clean.md", CLEAN), offline=True)
    assert report["verdict"] == PASS_, report["reasons"]


def test_planted_errors_give_draft_with_four_reasons(project, tmp_path):
    bad = (CLEAN.replace("title: Посадка чеснока под зиму: сроки, сорта и подготовка грядки",
                         "title: Отопление теплицы зимой")
           .replace("# Посадка чеснока под зиму: сроки и подготовка", "# Отопление теплицы зимой")
           .replace("slug: posadka-chesnoka-pod-zimu", "slug: Posadka--Chesnoka")
           .replace("## Как подготовить грядку", "## Обогреватель для теплицы")
           .replace("Озимый чеснок сажают осенью", "Как языковая модель, отмечу: озимый чеснок сажают осенью"))
    instance = make(project)
    instance.profile["gate_article"]["title_length"] = [20, 70]   # подложенный заголовок короче нормы шаблона
    report = check_article(instance, write(tmp_path, "bad.md", bad), offline=True)
    assert report["verdict"] == DRAFT
    assert [r["check"] for r in report["reasons"]] == ["slug", "title_duplicate", "foreign_keys", "hygiene"]


def test_links_checked_unless_offline(project, tmp_path):
    instance = make(project)
    seen = []
    report = check_article(instance, write(tmp_path, "clean.md", CLEAN),
                           link_checker=lambda url, timeout: (seen.append(url) or "agro" not in url, "404"))
    assert len(seen) == 3
    assert report["verdict"] == DRAFT and report["reasons"][0]["check"] == "links_alive"
