"""Профиль проекта (Т2, Т15): один файл `profile.yaml` в каталоге инстанса.

Проверяется при каждом запуске. Неизвестный ключ и пропущенный обязательный — ошибка с именем поля; значений по
умолчанию «молча» нет: всё, что машина использует, записано в профиле. Секреты — только именами переменных
(поля `*_env`) и путями к ключам; значение секрета в профиле — ошибка.

Версия профиля поднимается с каждым выпуском, который добавляет поля (Т15.1): 1.2.0 — версия 2. Схема версии 1
осталась в коде (`SCHEMA_V1`) только для перевода `profile migrate`; профиль старой версии в любой другой команде —
ошибка с названием команды перевода.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from .errors import ProfileError

SCHEMA_VERSION = 2
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

DIRECTION_V1 = L(name=F("str"), words=F("list"), weight=F("number"), quarter_quota=F("int"))

GATE_ARTICLE = S(
    levels=F("strmap"), fields=F("strmap"), title_length=F("list"), description_length=F("list"),
    faq_questions=F("list"), sales_links=F("list"), min_sources=F("int"), max_list_share=F("number"),
    link_timeout=F("int"), faq_heading=F("str"), cta_markers=F("list"), hygiene_patterns=F("list"),
    stop_phrases=F("list"))

COMMON = dict(
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
    gate_article=GATE_ARTICLE,
    stop_words=F("list"),
    brand_words=F("list"),
)

SCHEMA_V1 = S(**COMMON, budget=S(reserve=F("int"), max_quota=F("int", nullable=True)), directions=DIRECTION_V1)

ROLES = ("planner", "writer", "critic", "fact_checker", "judge", "cheap")
ROLE = S(primary=F("str"), fallback=F("str"))
AUTHOR = L(name=F("str"), role=F("str"), experience=F("str"), directions=F("list"), month_limit=F("int"))
ASSEMBLE_CHECKS = ("anchors", "toc", "tables", "required", "section_borders")

SCHEMA = S(
    **COMMON,
    budget=S(reserve=F("int"), max_quota=F("int", nullable=True), run_limit_rub=F("number"),
             month_limit_rub=F("number")),
    directions=L(name=F("str"), words=F("list"), weight=F("number"), quarter_quota=F("int"), volume=F("list")),
    models=S(**{role: ROLE for role in ROLES}),
    prepare=S(top_n=F("int"), serp_max_age_days=F("int"), volume_rule=F("str"), images_margin=F("number"),
              table_rule=F("str"), page_timeout=F("int"), page_max_kb=F("int"), page_cache_days=F("int"),
              skip_url_patterns=F("list"), main_blocks=F("list"), donors=F("list"), donor_sitemaps=F("int"),
              donor_top=F("int")),
    brief=S(authors=AUTHOR, bridge_share=F("list"), link_candidates=F("int")),
    assemble=S(adapter=F("str"), template=F("str"), schema_format=F("str"), required_elements=F("list"),
               levels=F("strmap")),
    publish=S(mode=F("str"), allow=F("str"), adapter=F("str"), repo_path=F("str"),
              cms=S(base_url=F("str"), user_env=F("str"), token_env=F("str")), hosts=F("list"),
              lock_minutes=F("int"), text_match=F("number"), browser=F("bool"), widths=F("list"),
              counter_domains=F("list"), window=F("list"), schedule_ahead_days=F("int"), journal_path=F("str")),
    indexing=S(allow=F("str"), indexnow=S(enabled=F("bool"), key_env=F("str"), key_location=F("str")),
               webmaster=S(enabled=F("bool")), status_days=F("list")),
    records=S(allow=F("str"), adapter=F("str")),
)
SCHEMAS = {1: SCHEMA_V1, 2: SCHEMA}

GATE_ARTICLE_CHECKS = (
    "title_length", "description_length", "slug", "title_duplicate", "structure", "skeleton", "foreign_keys",
    "foreign_mentions", "twin", "faq", "sales_links", "sources", "links_alive", "list_share", "hygiene", "stop_phrases", "schema")
LEVELS = ("block", "warn", "off")
VALUE_KINDS = ("file", "keysso", "none")
# Ступени прав (Т16.1): выше `files` — только строкой профиля по разрешению владельца.
ALLOW = {"publish": ("files", "commit", "publish"), "indexing": ("files", "write"), "records": ("files", "write")}
MIGRATE_HINT = "content-engine profile migrate --profile {path}"


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


def _pair(name: str, pair, kind=int):
    if (len(pair) != 2 or not all(isinstance(x, kind) and not isinstance(x, bool) for x in pair)
            or pair[0] > pair[1]):
        raise ProfileError(f"поле «{name}»: пара [от, до] {'целыми ' if kind is int else ''}числами")


def _one_of(name: str, value, allowed):
    if value not in allowed:
        raise ProfileError(f"поле «{name}»: одно из {tuple(allowed)}, получено «{value}»")


def _adapter(name: str, value: str, builtin: tuple):
    if value in builtin:
        return
    if value.startswith("module:") and value.count(":") == 2 and all(value.split(":")[1:]):
        return
    raise ProfileError(f"поле «{name}»: одно из {builtin} или «module:<путь от инстанса>:<класс>», "
                       f"получено «{value}»")


def _semantics_v1(data: dict):
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
        _pair(f"gate_article.{name}", data["gate_article"][name])
    t = data["thresholds"]
    if (t["section_block"] is None) != (t["section_warn"] is None):
        raise ProfileError("поля «thresholds.section_block» и «section_warn» задаются вместе")
    for name in ("body_length_ref", "section_length_ref"):
        if t[name] is not None and t[name] <= 0:
            raise ProfileError(f"поле «thresholds.{name}»: положительное число основ или null (без нормирования)")
    if data["queue"]["refill_at"] > data["queue"]["norm"]:
        raise ProfileError("поле «queue.refill_at»: порог пополнения не выше нормы запаса")


def _semantics_v2(data: dict):
    for role in ROLES:
        for kind in ("primary", "fallback"):
            if not data["models"][role][kind].strip():
                which = "основная" if kind == "primary" else "запасная"
                raise ProfileError(f"поле «models.{role}.{kind}»: у роли нужна {which} модель — основная "
                                   "недоступна, конвейер не встаёт")
    for i, d in enumerate(data["directions"]):
        _pair(f"directions[{i}].volume", d["volume"])
    pr = data["prepare"]
    _one_of("prepare.volume_rule", pr["volume_rule"], ("mean", "max"))
    _one_of("prepare.table_rule", pr["table_rule"], ("any", "majority", "all"))
    for name in ("top_n", "page_timeout", "page_max_kb", "donor_sitemaps", "donor_top"):
        if pr[name] <= 0:
            raise ProfileError(f"поле «prepare.{name}»: положительное число")
    if pr["serp_max_age_days"] >= data["thresholds"]["gate_valid_days"]:
        raise ProfileError("поле «prepare.serp_max_age_days»: меньше «thresholds.gate_valid_days» — иначе снимок "
                           "выдачи стареет вместе с отчётом гейта и `queue retake` недостижим")
    if not pr["main_blocks"]:
        raise ProfileError("поле «prepare.main_blocks»: хотя бы один тег основного блока (article, main)")
    _pair("brief.bridge_share", data["brief"]["bridge_share"], kind=(int, float))
    a = data["assemble"]
    _adapter("assemble.adapter", a["adapter"], ("html", "markdown"))
    _one_of("assemble.schema_format", a["schema_format"], ("json-ld", "microdata"))
    for name in ASSEMBLE_CHECKS:
        if name not in a["levels"]:
            raise ProfileError(f"пропущено обязательное поле «assemble.levels.{name}»")
    for name, level in a["levels"].items():
        _one_of(f"assemble.levels.{name}", name, ASSEMBLE_CHECKS)
        _one_of(f"assemble.levels.{name}", level, LEVELS)
    pub = data["publish"]
    _one_of("publish.mode", pub["mode"], ("manual", "direct"))
    _adapter("publish.adapter", pub["adapter"], ("none", "static_repo", "cms_api"))
    if pub["mode"] == "direct" and pub["adapter"] == "none":
        raise ProfileError("поле «publish.adapter»: прямая публикация (mode: direct) без публикатора — укажите "
                           "static_repo, cms_api или module:…")
    if not 0 < pub["text_match"] <= 1:
        raise ProfileError("поле «publish.text_match»: доля от 0 до 1")
    if len(pub["window"]) != 2 or not all(re.fullmatch(r"\d\d:\d\d", str(x)) for x in pub["window"]):
        raise ProfileError("поле «publish.window»: пара [\"ЧЧ:ММ\", \"ЧЧ:ММ\"]")
    for area in ALLOW:
        _one_of(f"{area}.allow", data[area]["allow"], ALLOW[area])
    _adapter("records.adapter", data["records"]["adapter"], ("none",))
    b = data["budget"]
    if b["run_limit_rub"] < 0 or b["month_limit_rub"] < 0:
        raise ProfileError("поля «budget.run_limit_rub», «month_limit_rub»: не меньше нуля (0 — платные вызовы запрещены)")
    if b["run_limit_rub"] > b["month_limit_rub"]:
        raise ProfileError("поле «budget.run_limit_rub»: предел прогона не выше месячного")


def validate(data, version: int = SCHEMA_VERSION) -> dict:
    """Проверить профиль по схеме версии; вернуть его же. Ошибка — `ProfileError` с именем поля."""
    if not isinstance(data, dict):
        raise ProfileError("профиль: ожидался раздел (словарь)")
    found = data.get("schema_version")
    if found != version:
        raise ProfileError(f"поле «schema_version»: {found}, пакет понимает {version}")
    _walk("", SCHEMAS[version], data)
    _semantics_v1(data)
    if version >= 2:
        _semantics_v2(data)
    return data


def read(path) -> dict:
    """Профиль как есть, без проверки схемы (для перевода)."""
    path = Path(path)
    if not path.is_file():
        raise ProfileError(f"профиль не найден: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"профиль {path.name}: YAML не читается — {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"профиль {path.name}: верхний уровень — не раздел")
    return data


def load(path) -> dict:
    data = read(path)
    version = data.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool) and version in SCHEMAS and version < SCHEMA_VERSION:
        raise ProfileError(f"профиль версии {version}: переведите командой `{MIGRATE_HINT.format(path=path)}`")
    return validate(data)


def template_text() -> str:
    return resources.files("content_engine.templates").joinpath("profile.template.yaml").read_text(encoding="utf-8")
