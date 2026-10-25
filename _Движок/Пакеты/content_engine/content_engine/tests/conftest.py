"""Синтетический проект для тестов: сайт example.ru о саде и даче, тексты придуманные."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from content_engine.core import profile as profile_mod

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 3, 2)


def base_profile() -> dict:
    data = yaml.safe_load(profile_mod.template_text())
    data["corpus"]["folders"] = [{
        "path": "texts", "pattern": "*.md", "exclude": ["README.md"],
        "fields": {"id": "id", "slug": "slug", "title": "title", "url": "url"},
        "url_template": "https://{domain}/{section}/{slug}", "section": "blog", "index_csv": ""}]
    data["directions"] = [{"name": "теплицы", "words": ["теплица"], "weight": 2, "quarter_quota": 4},
                          {"name": "полив", "words": ["полив"], "weight": 1, "quarter_quota": 0}]
    data["thresholds"]["section_block"] = 4
    data["thresholds"]["section_warn"] = 3
    return data


def write_article(folder: Path, slug: str, title: str, body: str, *, url: str | None = None, aid: str = ""):
    folder.mkdir(parents=True, exist_ok=True)
    head = {"id": aid or slug, "slug": slug, "title": title}
    if url:
        head["url"] = url
    text = "---\n" + yaml.safe_dump(head, allow_unicode=True) + "---\n\n" + body
    (folder / f"{slug}.md").write_text(text, encoding="utf-8", newline="\n")


GREENHOUSE = """# Отопление теплицы зимой

Тёплая теплица зимой даёт урожай круглый год.

## Чем топить теплицу

Печь, газовый обогреватель и тёплый пол. Печь в теплице греет воздух неравномерно.

## Полив теплицы зимой

Полив теплицы зимой — тёплой водой, утром. Полив теплицы капельный: вода не остывает, полив теплицы
экономный. Холодный полив теплицы зимой вредит корням, поэтому полив теплицы ведут из бочки в тепле.

## Частые вопросы

Нужна ли вентиляция теплицы зимой? Да, короткое проветривание днём.
"""

ROSES = """# Как укрывать розы на зиму

Розы укрывают после первых заморозков.

## Материалы для укрытия

Лапник, спанбонд, каркас из дуг.
"""

LAWN = """# Газон весной: первые работы

Газон весной прочёсывают граблями и подкармливают.

## Подкормка газона

Азотная подкормка газона весной ускоряет рост травы.
"""

SNAPSHOT = """Вступление о поливе сада без лишних слов.

Содержание

Когда поливать сад
Сколько воды нужно саду

Виджет: ответьте на вопросы

Когда поливать сад

Сад поливают вечером, когда солнце садится. Вечерний полив сада экономит воду.

Сколько воды нужно саду

Взрослому дереву нужно ведро воды в неделю.
"""


@pytest.fixture
def project(tmp_path) -> Path:
    """Каталог инстанса с профилем и корпусом из четырёх статей; возвращает путь к profile.yaml."""
    texts = tmp_path / "texts"
    write_article(texts, "otoplenie-teplicy", "Отопление теплицы зимой", GREENHOUSE)
    write_article(texts, "ukrytie-roz", "Как укрывать розы на зиму", ROSES)
    write_article(texts, "gazon-vesnoj", "Газон весной: первые работы", LAWN)
    write_article(texts, "poliv-sada", "Полив сада", SNAPSHOT, url="https://example.ru/garden/poliv-sada")
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(base_profile(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def set_profile(path: Path, change):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
