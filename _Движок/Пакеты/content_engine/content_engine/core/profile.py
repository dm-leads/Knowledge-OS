"""Профиль проекта (Т2): один файл `profile.yaml` в каталоге инстанса.

Проверяется при каждом запуске. Неизвестный ключ и пропущенный обязательный — ошибка с именем поля; значений по
умолчанию «молча» нет: всё, что машина использует, записано в профиле. Секреты — только именами переменных
(поля `*_env`) и путями к ключам; значение секрета в профиле — ошибка.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from .errors import ProfileError

SCHEMA_VERSION = 1
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True)
class F:
    """Поле профиля: тип и можно ли пусто (None)."""
    kind: str                 # str | int | float | number | bool | list | dict | strmap
    nullable: bool = False


def S(**fields):
    return {"__section__": True, **fields}


def L(**fields):
    return {"__list__": True, **fields}


CORPUS_FOLDER = L(
    path=F("str"), pattern=F("str"), exclude=F("list"), fields=F("strmap"), url_template=F("str"),
    section=F("str"), index_csv=F("str"))

DIRECTION = L(name=F("str"), words=F("list"), weight=F("number"), quarter_quota=F("int"))

SCHEMA = S(
    schema_version=F("int"),
    site=S(domains=F("list"), article_sections=F("list"), sales_sections=F("list"), sales_urls=F("list"),
           sitemap_url=F("str"), sitemap_file=F("str")),
    search=S(serp_bases=F("list"), commercial_domains=F("list"), competitors=F("list")),
    corpus=S(toc_marker=F("str"), folders=CORPUS_FOLDER),
    secrets=S(env_file=F("str")),
    sources=S(
        window_days=F("int"), junk_patterns=F("list"),
        gsc=S(enabled=F("bool"), site_url=F("str"), key_file=F("str")),
        webmaster=S(enabled=F("bool"), user_id=F("str"), host_id=F("str"), token_env=F("str")),
        keysso=S(enabled=F("bool"), key_env=F("str"), base=F("str"), site_keywords_limit=F("int"),
                 competitor_keywords_limit=F("int")),
        demand_file=S(enabled=F("bool"), path=F("str")),
        value=S(kind=F("str"), file=F("str"), average_check=F("number", nullable=True))),
    thresholds=S(
        key_overlap=F("number"), body_block=F("number"), body_warn=F("number"),
        section_block=F("number", nullable=True), section_warn=F("number", nullable=True),
        body_length_ref=F("int", nullable=True), section_length_ref=F("int", nullable=True),
        serp_common_urls=F("int"), sales_overlap=F("number"), head_term_cap=F("int"), title_overlap=F("number"),
        twin_shingles=F("number"), commercial_min=F("int"), serp_cache_days=F("int"), gate_valid_days=F("int"),
        reservation_days=F("int"), money_days=F("int"), min_impressions=F("number")),
    queue=S(per_day=F("number"), norm=F("int"), refill_at=F("int"), expected_rejection=F("number"),
            require_bet=F("bool"), max_formulations=F("int"), lead_words=F("list"), commercial_words=F("list")),
    budget=S(reserve=F("int"), max_quota=F("int", nullable=True)),
    directions=DIRECTION,
    gate_article=S(
        levels=F("strmap"), fields=F("strmap"), title_length=F("list"), description_length=F("list"), faq_questions=F("list"),
        sales_links=F("list"), min_sources=F("int"), max_list_share=F("number"), link_timeout=F("int"),
        faq_heading=F("str"), cta_markers=F("list"), hygiene_patterns=F("list"), stop_phrases=F("list")),
    stop_words=F("list"),
    brand_words=F("list"),
)

GATE_ARTICLE_CHECKS = (
    "title_length", "description_length", "slug", "title_duplicate", "structure", "skeleton", "foreign_keys",
    "foreign_mentions", "twin", "faq", "sales_links", "sources", "links_alive", "list_share", "hygiene", "stop_phrases", "schema")
LEVELS = ("block", "warn", "off")
VALUE_KINDS = ("file", "keysso", "none")


def _check_type(path: str, field: F, value):
    if value is None:
        if field.nullable:
            return
        raise ProfileError(f"поле «{path}»: пусто, а значение обязательно")
    ok = {
        "str": isinstance(value, str),
        "int": isinstance(value, int) and not isinstance(value, bool),
        "float": isinstance(value, float),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "bool": isinstance(value, bool),
        "list": isinstance(value, list),
        "dict": isinstance(value, dict),
        "strmap": isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                  for k, v in value.items()),
    }[field.kind]
    if not ok:
        raise ProfileError(f"поле «{path}»: ожидался тип {field.kind}, получено {type(value).__name__}")
    if path.endswith("_env") and value and not ENV_NAME.match(value):
        raise ProfileError(f"поле «{path}»: здесь пишется имя переменной окружения, а не значение секрета")


def _walk(path: str, spec: dict, data):
    if spec.get("__list__"):
        if not isinstance(data, list):
            raise ProfileError(f"поле «{path}»: ожидался список")
        for i, item in enumerate(data):
            _walk_section(f"{path}[{i}]", spec, item)
        return
    _walk_section(path, spec, data)


def _walk_section(path: str, spec: dict, data):
    if not isinstance(data, dict):
        raise ProfileError(f"поле «{path or 'профиль'}»: ожидался раздел (словарь)")
    fields = {k: v for k, v in spec.items() if not k.startswith("__")}
    for key in data:
        if key not in fields:
            raise ProfileError(f"неизвестное поле «{_join(path, key)}»")
    for key, sub in fields.items():
        name = _join(path, key)
        if key not in data:
            raise ProfileError(f"пропущено обязательное поле «{name}»")
        if isinstance(sub, F):
            _check_type(name, sub, data[key])
        else:
            _walk(name, sub, data[key])


def _join(path, key):
    return f"{path}.{key}" if path else key


def _semantics(data: dict):
    if data["schema_version"] != SCHEMA_VERSION:
        raise ProfileError(f"поле «schema_version»: {data['schema_version']}, пакет понимает {SCHEMA_VERSION}")
    if not data["site"]["domains"]:
        raise ProfileError("поле «site.domains»: нужен хотя бы один домен")
    if data["sources"]["value"]["kind"] not in VALUE_KINDS:
        raise ProfileError(f"поле «sources.value.kind»: одно из {VALUE_KINDS}")
    for name, level in data["gate_article"]["levels"].items():
        if name not in GATE_ARTICLE_CHECKS:
            raise ProfileError(f"неизвестное поле «gate_article.levels.{name}»")
        if level not in LEVELS:
            raise ProfileError(f"поле «gate_article.levels.{name}»: одно из {LEVELS}")
    for name in GATE_ARTICLE_CHECKS:
        if name not in data["gate_article"]["levels"]:
            raise ProfileError(f"пропущено обязательное поле «gate_article.levels.{name}»")
    for name in ("title_length", "description_length", "faq_questions", "sales_links"):
        pair = data["gate_article"][name]
        if len(pair) != 2 or not all(isinstance(x, int) for x in pair) or pair[0] > pair[1]:
            raise ProfileError(f"поле «gate_article.{name}»: пара [от, до] целыми числами")
    t = data["thresholds"]
    if (t["section_block"] is None) != (t["section_warn"] is None):
        raise ProfileError("поля «thresholds.section_block» и «section_warn» задаются вместе")
    for name in ("body_length_ref", "section_length_ref"):
        if t[name] is not None and t[name] <= 0:
            raise ProfileError(f"поле «thresholds.{name}»: положительное число основ или null (без нормирования)")
    if data["queue"]["refill_at"] > data["queue"]["norm"]:
        raise ProfileError("поле «queue.refill_at»: порог пополнения не выше нормы запаса")


def validate(data) -> dict:
    """Проверить профиль; вернуть его же. Ошибка — `ProfileError` с именем поля."""
    _walk("", SCHEMA, data)
    _semantics(data)
    return data


def load(path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ProfileError(f"профиль не найден: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"профиль {path.name}: YAML не читается — {exc}") from exc
    return validate(data)


def template_text() -> str:
    return resources.files("content_engine.templates").joinpath("profile.template.yaml").read_text(encoding="utf-8")
