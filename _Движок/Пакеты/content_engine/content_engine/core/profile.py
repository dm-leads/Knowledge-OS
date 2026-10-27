"""Профиль проекта (Т2, Т15): один файл `profile.yaml` в каталоге инстанса.

Проверяется при каждом запуске. Неизвестный ключ и пропущенный обязательный — ошибка с именем поля; значений по
умолчанию «молча» нет: всё, что машина использует, записано в профиле. Секреты — только именами переменных
(поля `*_env`) и путями к ключам; значение секрета в профиле — ошибка.

Версия профиля поднимается с каждым выпуском, который добавляет поля (Т15.1): 1.2.0 — версия 2, 1.3.0 — версия 3,
1.4.0 — версия 4. Схемы прошлых версий (`SCHEMA_V1`, `SCHEMA_V2`, `SCHEMA_V3`) и их наборы проверок остались в коде только для перевода `profile
migrate`: общие разделы и списки проверок разведены по версиям, иначе поле новой версии сломало бы проверку старого
профиля перед переводом. Профиль старой версии в любой другой команде — ошибка с названием команды перевода.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from .errors import ProfileError

SCHEMA_VERSION = 4
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
ASSEMBLE_CHECKS_V2 = ("anchors", "toc", "tables", "required", "section_borders")
ASSEMBLE_CHECKS = ASSEMBLE_CHECKS_V2 + ("comments",)       # Н5: публикуемый HTML без комментариев
ASSEMBLE_BY_VERSION = {2: ASSEMBLE_CHECKS_V2, 3: ASSEMBLE_CHECKS, 4: ASSEMBLE_CHECKS}
GROWTH_KINDS = ("none", "markdown", "file")
WORDSTAT_SOURCES = ("keysso", "cloud")
SERP_SOURCES = ("keysso", "yandex_search")
TEXT_LEVELS = ("обычный", "усиленный")
IMAGE_KINDS = ("cover", "illustration", "scheme", "product")
TOPIC_SUBS = ("id",)
PUBLISHED_SUBS = ("slug", "h1", "id")

PART1 = dict(
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
BUDGET_V2 = dict(reserve=F("int"), max_quota=F("int", nullable=True), run_limit_rub=F("number"),
                 month_limit_rub=F("number"))
SCHEMA_V2 = S(**COMMON, budget=S(**BUDGET_V2), **PART1)

# Версия 3 (часть 2 волны 2, выпуск 1.3.0): общие разделы sources, queue, gate_article получают новые поля — они
# строятся заново из полей версии 2, а не правкой COMMON, чтобы схемы версий 1 и 2 не менялись.
SOURCES_V3 = dict(COMMON["sources"])
SOURCES_V3.update(
    growth=S(kind=F("str"), path=F("str")),
    client_questions=S(enabled=F("bool"), path=F("str")),
    wordstat=S(order=F("list"), region=F("str"), cloud_key_env=F("str"), cloud_folder_env=F("str"),
               cloud_price_rub=F("number")),
    suggest=S(enabled=F("bool"), limit=F("int")),
    serp_order=F("list"),
    yandex_search=S(key_env=F("str"), folder_env=F("str"), price_rub=F("number"), regions=F("strmap")))
QUEUE_V3 = dict(COMMON["queue"])
QUEUE_V3["pace"] = S(domain_since=F("str"), new_domain_days=F("int"), new_domain_max_per_day=F("number"))
GATE_ARTICLE_V3 = dict(GATE_ARTICLE)
GATE_ARTICLE_V3.update(rhythm_min_cv=F("number"), ai_tone_patterns=F("list"), norm_patterns=F("list"),
                       answer_words=F("int"))
COMMON_V3 = dict(COMMON, sources=SOURCES_V3, queue=QUEUE_V3, gate_article=GATE_ARTICLE_V3)

SCHEMA_V3 = S(
    **COMMON_V3,
    budget=S(**BUDGET_V2, usd_rub=F("number"), usd_rub_source=F("str")),
    **PART1,
    paths=S(allow=F("str"), transfer=F("str"), topic_dir=F("str"), published_dir=F("str"),
            published_files=F("strmap")),
    judge=S(top=F("int"), embeddings=S(kind=F("str"), model=F("str"), key_env=F("str"),
                                       price_per_1k_rub=F("number"))),
    write=S(rules=F("list"), level=F("str")),
    facts=S(enabled=F("bool"), ttl_days=F("int"), mode=F("str")),
    niche=S(kind=F("str"), min_sources=F("int"), disclaimer=F("str"), level=F("str")),
    images=S(allow=F("str"), providers=F("strmap"), library=S(index=F("str")), fill_rule=F("str"),
             upload=F("str"), template=F("str"), after_h2=F("list"), max_kb=F("int"), stop_categories=F("list"),
             cover=F("bool")),
    onboard=S(min_cluster_frequency=F("int"), sample_pages=F("int"), answers=F("strmap")),
)

# Версия 4 (волна 3, часть 1, выпуск 1.4.0): пульс, книга ставок и замер, экономика, аудит, расписание, план недели.
# Разделы `publish` и `directions` получают поля — строятся заново из версии 3, а не правкой PART1.
PUBLISH_V4 = dict(PART1["publish"], verify_retries=F("int"), verify_retry_seconds=F("int"))
DIRECTIONS_V4 = dict(PART1["directions"], hypothesis_id=F("str"))
SCHEMA = dict(
    SCHEMA_V3,
    publish=PUBLISH_V4,
    directions=DIRECTIONS_V4,
    monitor=S(positions=F("str"), keys=F("str"), drop=F("int"), tops=F("list"), silence_intervals=F("number"),
              stock_yellow_days=F("number"), audit_stale_days=F("int"), drift_shrink=F("number"),
              index_days_alarm=F("int")),
    notify=S(allow=F("str"), kind=F("str"), url_env=F("str"), chat_env=F("str")),
    bets=S(money=S(kind=F("str"), file=F("str")), min_step=F("int"), proposal_dir=F("str"),
           backfill_section=F("str")),
    economics=S(close_min_articles=F("int"), close_window=F("int"), close_leads_max=F("number")),
    audit=S(checklist=F("str"), false_alarm=S(rate=F("number"), as_of=F("str"), source=F("str")),
            false_alarm_max=F("number"), neighbours=F("int"), inspect_limit=F("int"), speed=F("str"),
            delay_seconds=F("number")),
    schedule=S(allow=F("str"), kind=F("str"), python=F("str"), on_boot=F("bool"),
               jobs=L(name=F("str"), command=F("str"), every=F("str"), at=F("str"), weekday=F("str"))),
    week_plan=S(min=F("int"), max=F("int")),
)
SCHEMAS = {1: SCHEMA_V1, 2: SCHEMA_V2, 3: SCHEMA_V3, 4: SCHEMA}

GATE_ARTICLE_CHECKS_V2 = (
    "title_length", "description_length", "slug", "title_duplicate", "structure", "skeleton", "foreign_keys",
    "foreign_mentions", "twin", "faq", "sales_links", "sources", "links_alive", "list_share", "hygiene", "stop_phrases", "schema")
# Часть 2: текст (Т28.2), вычитка и ниша (Т29.3–Т29.4), картинки (Т30)
GATE_ARTICLE_CHECKS = GATE_ARTICLE_CHECKS_V2 + (
    "rhythm", "ai_tone", "volume", "norm_links", "internal_links", "ai_answers", "facts", "niche", "image_alt",
    "image_credits")
GATE_CHECKS_BY_VERSION = {1: GATE_ARTICLE_CHECKS_V2, 2: GATE_ARTICLE_CHECKS_V2, 3: GATE_ARTICLE_CHECKS, 4: GATE_ARTICLE_CHECKS}
LEVELS = ("block", "warn", "off")
VALUE_KINDS = ("file", "keysso", "none")
# Ступени прав (Т16.1): выше `files` — только строкой профиля по разрешению владельца.
ALLOW_V2 = {"publish": ("files", "commit", "publish"), "indexing": ("files", "write"), "records": ("files", "write")}
# Часть 2: загрузка картинок (Т30) и передача статьи в папку проекта (Н1) — свои ступени
ALLOW_V3 = dict(ALLOW_V2, images=("files", "write"), paths=("files", "write"))
# Волна 3: сводка в мессенджер (Т34.5) и расписание (Т42.1) — запись вовне со своими ступенями
ALLOW = dict(ALLOW_V3, notify=("files", "write"), schedule=("files", "write"))
ALLOW_BY_VERSION = {2: ALLOW_V2, 3: ALLOW_V3, 4: ALLOW}
NOTIFY_KINDS = ("none", "lark_webhook", "telegram")
MONEY_KINDS = ("none", "file")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
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


def _semantics_v1(data: dict, version: int = 1):
    if not data["site"]["domains"]:
        raise ProfileError("поле «site.domains»: нужен хотя бы один домен")
    if data["sources"]["value"]["kind"] not in VALUE_KINDS:
        raise ProfileError(f"поле «sources.value.kind»: одно из {VALUE_KINDS}")
    checks = GATE_CHECKS_BY_VERSION[version]
    for name, level in data["gate_article"]["levels"].items():
        if name not in checks:
            raise ProfileError(f"неизвестное поле «gate_article.levels.{name}»")
        if level not in LEVELS:
            raise ProfileError(f"поле «gate_article.levels.{name}»: одно из {LEVELS}")
    for name in checks:
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


def _semantics_v2(data: dict, version: int = 2):
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
    assemble_checks = ASSEMBLE_BY_VERSION[version]
    for name in assemble_checks:
        if name not in a["levels"]:
            raise ProfileError(f"пропущено обязательное поле «assemble.levels.{name}»")
    for name, level in a["levels"].items():
        _one_of(f"assemble.levels.{name}", name, assemble_checks)
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
    allow = ALLOW_BY_VERSION[version]
    for area in allow:
        _one_of(f"{area}.allow", data[area]["allow"], allow[area])
    _adapter("records.adapter", data["records"]["adapter"], ("none",))
    b = data["budget"]
    if b["run_limit_rub"] < 0 or b["month_limit_rub"] < 0:
        raise ProfileError("поля «budget.run_limit_rub», «month_limit_rub»: не меньше нуля (0 — платные вызовы запрещены)")
    if b["run_limit_rub"] > b["month_limit_rub"]:
        raise ProfileError("поле «budget.run_limit_rub»: предел прогона не выше месячного")


def _template_subs(name: str, value: str, allowed: tuple):
    for sub in re.findall(r"\{([^}]*)\}", value):
        if sub not in allowed:
            names = ", ".join("{" + a + "}" for a in allowed)
            raise ProfileError(f"поле «{name}»: подстановка {{{sub}}} не из {names}")


def _semantics_v3(data: dict):
    p = data["paths"]
    if p["topic_dir"].rstrip("/").split("/")[-1] != "{id}":
        raise ProfileError("поле «paths.topic_dir»: последняя часть пути — {id} (у каждой темы своя папка)")
    _template_subs("paths.topic_dir", p["topic_dir"], TOPIC_SUBS)
    _template_subs("paths.published_dir", p["published_dir"], PUBLISHED_SUBS)
    for name in ("topic_dir", "published_dir"):
        if p[name] and re.match(r"^([A-Za-z]:|[/\\])", p[name]):
            raise ProfileError(f"поле «paths.{name}»: путь от каталога инстанса, не абсолютный")
    for role in ("article", "schema"):
        if not p["published_files"].get(role):
            raise ProfileError(f"поле «paths.published_files.{role}»: имя файла в папке выпущенной статьи")
    _adapter("paths.transfer", p["transfer"], ("builtin",))
    src = data["sources"]
    _one_of("sources.growth.kind", src["growth"]["kind"], GROWTH_KINDS)
    if src["growth"]["kind"] != "none" and not src["growth"]["path"]:
        raise ProfileError("поле «sources.growth.path»: папка хранилища Движка роста или карточек гипотез")
    for i, name in enumerate(src["wordstat"]["order"]):
        _one_of(f"sources.wordstat.order[{i}]", name, WORDSTAT_SOURCES)
    if "cloud" in src["wordstat"]["order"] and not src["wordstat"]["cloud_key_env"]:
        raise ProfileError("поле «sources.wordstat.cloud_key_env»: облако в порядке источников — нужно имя переменной")
    if not src["serp_order"]:
        raise ProfileError("поле «sources.serp_order»: хотя бы один источник выдачи")
    for i, name in enumerate(src["serp_order"]):
        _one_of(f"sources.serp_order[{i}]", name, SERP_SOURCES)
    if "yandex_search" in src["serp_order"] and not src["yandex_search"]["key_env"]:
        raise ProfileError("поле «sources.yandex_search.key_env»: источник в порядке выдачи — нужно имя переменной")
    # платный источник с ценой 0 машина сочла бы бесплатным — цена из тарифа обязательна
    if "cloud" in src["wordstat"]["order"] and src["wordstat"]["cloud_price_rub"] <= 0:
        raise ProfileError("поле «sources.wordstat.cloud_price_rub»: облако платное — цена запроса из тарифа, больше 0")
    if "yandex_search" in src["serp_order"] and src["yandex_search"]["price_rub"] <= 0:
        raise ProfileError("поле «sources.yandex_search.price_rub»: источник платный — цена запроса из тарифа, больше 0")
    if src["suggest"]["limit"] <= 0:
        raise ProfileError("поле «sources.suggest.limit»: положительное число")
    if src["wordstat"]["cloud_price_rub"] < 0 or src["yandex_search"]["price_rub"] < 0:
        raise ProfileError("поля «sources.wordstat.cloud_price_rub», «sources.yandex_search.price_rub»: не меньше нуля")
    j = data["judge"]
    if j["top"] <= 0:
        raise ProfileError("поле «judge.top»: положительное число")
    _one_of("judge.embeddings.kind", j["embeddings"]["kind"], ("lexical", "api"))
    if j["embeddings"]["kind"] == "api":
        if not j["embeddings"]["key_env"]:
            raise ProfileError("поле «judge.embeddings.key_env»: эмбеддинги через API — нужно имя переменной ключа")
        _adapter("judge.embeddings.model", j["embeddings"]["model"], ())   # провайдер — адаптер инстанса
        if j["embeddings"]["price_per_1k_rub"] <= 0:
            raise ProfileError("поле «judge.embeddings.price_per_1k_rub»: API платный — цена из тарифа, больше 0")
    _one_of("write.level", data["write"]["level"], TEXT_LEVELS)
    _one_of("niche.level", data["niche"]["level"], TEXT_LEVELS)
    _one_of("facts.mode", data["facts"]["mode"], ("separate", "human"))
    if data["facts"]["ttl_days"] <= 0:
        raise ProfileError("поле «facts.ttl_days»: положительное число дней")
    g = data["gate_article"]
    if not 0 <= g["rhythm_min_cv"] <= 1:
        raise ProfileError("поле «gate_article.rhythm_min_cv»: доля от 0 до 1")
    if g["answer_words"] <= 0:
        raise ProfileError("поле «gate_article.answer_words»: положительное число слов")
    for name in ("ai_tone_patterns", "norm_patterns"):
        for pattern in g[name]:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ProfileError(f"поле «gate_article.{name}»: «{pattern}» — не регулярное выражение ({exc})")
    im = data["images"]
    for kind in im["providers"]:
        if kind not in IMAGE_KINDS:
            raise ProfileError(f"неизвестное поле «images.providers.{kind}»; виды — {IMAGE_KINDS}")
    for kind in IMAGE_KINDS:
        if kind not in im["providers"]:
            raise ProfileError(f"пропущено обязательное поле «images.providers.{kind}»")
        _adapter(f"images.providers.{kind}", im["providers"][kind], ("none", "template", "library"))
    if "library" in im["providers"].values() and not im["library"]["index"]:
        raise ProfileError("поле «images.library.index»: провайдер library — нужен индекс готовых картинок (CSV)")
    _adapter("images.upload", im["upload"], ("none",))
    _one_of("images.fill_rule", im["fill_rule"], ("top", "after_h2"))
    if im["max_kb"] <= 0:
        raise ProfileError("поле «images.max_kb»: положительное число")
    if data["budget"]["usd_rub"] < 0:
        raise ProfileError("поле «budget.usd_rub»: курс не меньше нуля")
    pace = data["queue"]["pace"]
    if pace["domain_since"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pace["domain_since"]):
        raise ProfileError("поле «queue.pace.domain_since»: дата ГГГГ-ММ-ДД или пусто")
    for key in data["onboard"]["answers"]:
        _one_of(f"onboard.answers.{key}", key, ("vychitka", "rhythm"))


def _semantics_v4(data: dict):
    m = data["monitor"]
    _adapter("monitor.positions", m["positions"], ("consoles", "none"))
    _one_of("monitor.keys", m["keys"], ("demand_map",))
    for name in ("drop", "audit_stale_days", "index_days_alarm"):
        if m[name] <= 0:
            raise ProfileError(f"поле «monitor.{name}»: положительное число")
    if m["silence_intervals"] <= 0 or m["stock_yellow_days"] < 0:
        raise ProfileError("поля «monitor.silence_intervals», «stock_yellow_days»: положительное число")
    if not 0 < m["drift_shrink"] < 1:
        raise ProfileError("поле «monitor.drift_shrink»: доля от 0 до 1")
    if not m["tops"] or not all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in m["tops"]):
        raise ProfileError("поле «monitor.tops»: список положительных целых (например [10, 30])")
    n = data["notify"]
    _adapter("notify.kind", n["kind"], NOTIFY_KINDS)
    if n["kind"] != "none" and not n["url_env"]:
        raise ProfileError("поле «notify.url_env»: сводка включена — нужно имя переменной адреса или токена")
    if n["kind"] == "telegram" and not n["chat_env"]:
        raise ProfileError("поле «notify.chat_env»: Telegram — нужно имя переменной с id чата")
    b = data["bets"]
    _adapter("bets.money.kind", b["money"]["kind"], MONEY_KINDS)
    if b["money"]["kind"] == "file" and not b["money"]["file"]:
        raise ProfileError("поле «bets.money.file»: kind: file — нужен путь к CSV денег по окнам")
    if b["min_step"] not in (1, 2, 3):
        raise ProfileError("поле «bets.min_step»: ступень лесенки 1, 2 или 3")
    if not b["proposal_dir"]:
        raise ProfileError("поле «bets.proposal_dir»: папка файлов-предложений для Движка роста")
    e = data["economics"]
    if e["close_min_articles"] <= 0 or e["close_window"] <= 0 or e["close_leads_max"] < 0:
        raise ProfileError("поля «economics.*»: положительные числа (close_leads_max — не меньше нуля)")
    a = data["audit"]
    rate = a["false_alarm"]["rate"]
    if rate != -1 and not 0 <= rate <= 1:
        raise ProfileError("поле «audit.false_alarm.rate»: доля от 0 до 1 или -1 (не замерено)")
    if rate != -1 and not (a["false_alarm"]["as_of"] and a["false_alarm"]["source"]):
        raise ProfileError("поля «audit.false_alarm.as_of», «source»: у замера доли — дата и источник")
    if not 0 < a["false_alarm_max"] <= 1:
        raise ProfileError("поле «audit.false_alarm_max»: доля от 0 до 1")
    for name in ("neighbours", "inspect_limit"):
        if a[name] <= 0:
            raise ProfileError(f"поле «audit.{name}»: положительное число")
    if a["delay_seconds"] < 0:
        raise ProfileError("поле «audit.delay_seconds»: не меньше нуля")
    if not a["checklist"]:
        raise ProfileError("поле «audit.checklist»: путь к техническому чек-листу от инстанса")
    _adapter("audit.speed", a["speed"], ("none",))
    s = data["schedule"]
    _adapter("schedule.kind", s["kind"], ("windows", "cron"))
    if not s["python"].strip():
        raise ProfileError("поле «schedule.python»: чем запускать Python (например «py -3»)")
    names = set()
    for i, job in enumerate(s["jobs"]):
        where = f"schedule.jobs[{i}]"
        if not re.fullmatch(r"[a-z][a-z0-9-]*", job["name"]) or job["name"] in names:
            raise ProfileError(f"поле «{where}.name»: латиница в нижнем регистре, без повторов")
        names.add(job["name"])
        _one_of(f"{where}.every", job["every"], ("day", "week"))
        if not re.fullmatch(r"\d\d:\d\d", job["at"]):
            raise ProfileError(f"поле «{where}.at»: время «ЧЧ:ММ»")
        if job["every"] == "week":
            _one_of(f"{where}.weekday", job["weekday"], WEEKDAYS)
        elif job["weekday"]:
            raise ProfileError(f"поле «{where}.weekday»: только у every: week")
        _scheduled_command(where, job["command"])
    w = data["week_plan"]
    if not 0 < w["min"] <= w["max"]:
        raise ProfileError("поля «week_plan.min», «max»: 0 < min ≤ max")


def _scheduled_command(where: str, command: str):
    """Задание расписания — подкоманда машины с флагом `--unattended`: иначе запуск без человека встанет на коде 2."""
    from ..cli import build_parser
    from .contract import flags, leaves
    found = dict(leaves(build_parser())).get(command.strip())
    if found is None:
        raise ProfileError(f"поле «{where}.command»: «{command}» — нет такой команды машины")
    if "--unattended" not in flags(found):
        raise ProfileError(f"поле «{where}.command»: у «{command}» нет --unattended — по расписанию её не запускать")


def validate(data, version: int = SCHEMA_VERSION) -> dict:
    """Проверить профиль по схеме версии; вернуть его же. Ошибка — `ProfileError` с именем поля."""
    if not isinstance(data, dict):
        raise ProfileError("профиль: ожидался раздел (словарь)")
    found = data.get("schema_version")
    if found != version:
        raise ProfileError(f"поле «schema_version»: {found}, пакет понимает {version}")
    _walk("", SCHEMAS[version], data)
    _semantics_v1(data, version)
    if version >= 2:
        _semantics_v2(data, version)
    if version >= 3:
        _semantics_v3(data)
    if version >= 4:
        _semantics_v4(data)
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


def template_text(version: int = SCHEMA_VERSION) -> str:
    """Шаблон профиля версии; шаблоны прошлых версий нужны переводу: поля шага берутся из шаблона его версии."""
    name = "profile.template.yaml" if version == SCHEMA_VERSION else f"profile.template.v{version}.yaml"
    return resources.files("content_engine.templates").joinpath(name).read_text(encoding="utf-8")
