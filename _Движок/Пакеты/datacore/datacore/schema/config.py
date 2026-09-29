"""Конфигурация инстанса и карта источников: единственное место, где переносимая часть узнаёт имена инстанса —
и только как значения из YAML, не как код."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .pii import normalize_phone


@dataclass(frozen=True)
class CrossScopeRule:
    allowed: bool
    proof: str


@dataclass(frozen=True)
class InstanceConfig:
    instance: str
    timezone: str
    duckdb_path: Path
    postgres_url_env: str
    pii_key_env: str
    company_lines: frozenset[str]          # нормализованные номера линий компании
    forbidden_names: tuple[str, ...]
    scopes: tuple[str, ...]
    cross_scope: dict[str, CrossScopeRule]
    phone_in_text_columns: tuple[str, ...] = ()
    crm: dict | None = None                # секция загрузчика класса D: имена полей источника живут только здесь
    tracking: dict | None = None           # секция загрузчика класса C: кабинеты, эндпоинты, темп (имена поставщика — только тут)
    web_logs: dict | None = None           # проба сырых визитов веб-аналитики (этап 3), загрузчик — этап 6
    money: dict | None = None              # секция загрузчика денег (этап 4): имена полей источника живут только здесь
    spend: dict | None = None              # секция загрузчика рекламного расхода (этап 6): кабинет, определение суммы, ставка НДС
    contours: dict | None = None           # наборы отбора сделок (этап 5): имя → {base, exclude}. Любой отбор — параметр, а не ветка в коде (Я12)
    motivation: dict | None = None         # условия договорённости о мотивации: baseline, шкала, гарантия. Параметры соглашения, не кода
    mql: dict | None = None                # правило MQL: воронки квалификации, статусы, целевой маршрут, настоящие причины отказа (Я12)
    known_answers_tolerance: tuple[float, float] = (0.0, 0.0)   # (абсолютный, относительный) — для ответов чужих систем

    def cross_scope_proof(self, metric: str) -> str | None:
        rule = self.cross_scope.get(metric)
        return rule.proof if rule and rule.allowed and rule.proof else None

    def allowed_values(self) -> dict[str, frozenset[str]]:
        """Белые списки для колонок с номерами компании — аргумент allowed_values писателя (К6)."""
        return {col: self.company_lines for col in ("callee_line", *self.phone_in_text_columns)}


def load_config(path: Path) -> InstanceConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = {}
    for metric, r in raw.get("cross_scope", {}).items():
        if r.get("allowed") and not r.get("proof"):
            raise ValueError(f"cross_scope.{metric}: разрешено складывать без доказательства")
        rules[metric] = CrossScopeRule(bool(r.get("allowed")), r.get("proof", ""))
    return InstanceConfig(
        instance=raw["instance"], timezone=raw["timezone"],
        duckdb_path=(Path(path).parent.parent / "Скрипты" / raw["storage"]["duckdb_path"]).resolve(),
        postgres_url_env=raw["storage"]["postgres_url_env"],
        pii_key_env=raw["pii"]["key_env"],
        company_lines=frozenset(normalize_phone(str(x)) for x in raw["pii"].get("company_lines", [])),
        forbidden_names=tuple(str(x) for x in raw["boundary"]["forbidden_names"]),
        scopes=tuple(raw["scopes"]), cross_scope=rules,
        phone_in_text_columns=tuple(raw["pii"].get("phone_in_text_columns", [])),
        crm=raw.get("crm"), tracking=raw.get("tracking"), web_logs=raw.get("web_logs"), money=raw.get("money"), spend=raw.get("spend"),
        contours=raw.get("contours"), motivation=raw.get("motivation"), mql=raw.get("mql"),
        known_answers_tolerance=(float(raw.get("known_answers_tolerance", {}).get("abs", 0)),
                                 float(raw.get("known_answers_tolerance", {}).get("rel", 0))))


def pii_key(cfg: InstanceConfig, env: dict) -> bytes:
    value = env.get(cfg.pii_key_env)
    if not value:
        raise ValueError(f"в окружении нет переменной {cfg.pii_key_env} — ключ ПДн инстанса")
    return value.encode("utf-8")


@dataclass(frozen=True)
class SourceEntry:
    name: str
    cls: str
    status: str
    stage: int
    secret_env_names: tuple[str, ...]


def load_source_map(path: Path) -> list[SourceEntry]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [SourceEntry(s["name"], s["class"], s["status"], int(s["stage"]), tuple(s.get("secret_env_names", [])))
            for s in raw["sources"]]


def secret_names_present(entries: list[SourceEntry], known_env_names) -> None:
    known = set(known_env_names)
    for e in entries:
        unknown = [x for x in e.secret_env_names if x not in known]
        if unknown:
            raise ValueError(f"источник «{e.name}»: имён переменных нет в файле секретов: {', '.join(unknown)}")
