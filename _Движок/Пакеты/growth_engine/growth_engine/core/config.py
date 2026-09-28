"""Конфигурация инстанса: цель, форма бизнеса, уровни, потоки, правила сложения метрик и горизонт когорты.

Исключение из стража 3 (сложение между кабинетами) принимается только со ссылкой на проверку.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import GuardViolation

BUSINESS_FORM = {
    "purchase": {"разовая", "повторная"},
    "ticket": {"высокий", "низкий"},
    "market": {"b2c", "b2b", "смешанный"},
    "pmf": {"нет", "слабый", "сильный"},
    "manual_share": {"высокая", "низкая"},
    "deal_cycle": {"длинный", "короткий"},
}


@dataclass(frozen=True)
class MetricRule:
    name: str
    level: str
    period_additive: bool
    cross_scope_additive: bool
    proof: str
    summable: bool = True     # среднее и доля не складываются ни по окнам, ни по разрезам


@dataclass(frozen=True)
class InstanceConfig:
    goal_metric: str
    goal_window_months: int
    goal_baseline: float
    target_periods: tuple[str, ...]
    business_form: dict
    levels: tuple[str, ...]
    flows: tuple[str, ...]
    metrics: dict
    storage_adapter: str
    cohort_horizon_days: int | None = None   # цикл сделки: горизонт когорты и дозревания окна (П2, Т6)
    cohort_horizon_proof: str = ""
    storage_link_domains: tuple[str, ...] = ()   # страж 12: ссылки в хранилище — только на эти домены

    def rule(self, metric: str) -> MetricRule:
        if metric not in self.metrics:
            raise GuardViolation(3, f"метрика «{metric}» не объявлена в конфигурации")
        return self.metrics[metric]


def _link_domains(storage: dict) -> tuple[str, ...]:
    domains = storage.get("allowed_link_domains") or []
    if not isinstance(domains, list) or not all(isinstance(d, str) and d.strip() and "/" not in d and ":" not in d
                                                for d in domains):
        raise ValueError("storage.allowed_link_domains: нужен список доменов без схемы и пути")
    return tuple(d.strip().lower() for d in domains)


def load_config(path) -> InstanceConfig:
    return parse_config(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def parse_config(raw: dict) -> InstanceConfig:
    form = raw["business_form"]
    for key, allowed in BUSINESS_FORM.items():
        if form.get(key) not in allowed:
            raise ValueError(f"форма бизнеса: «{key}» = {form.get(key)!r}, допустимо {sorted(allowed)}")
    levels = tuple(raw["levels"])
    metrics = {}
    for name, m in raw["metrics"].items():
        if m["level"] not in levels:
            raise ValueError(f"метрика «{name}»: уровень «{m['level']}» не объявлен в levels")
        cross = bool(m.get("cross_scope_additive", False))
        proof = str(m.get("proof", "")).strip()
        if cross and not proof:
            raise ValueError(f"метрика «{name}»: исключение из стража 3 без ссылки на проверку")
        metrics[name] = MetricRule(name, m["level"], bool(m.get("period_additive", False)), cross, proof,
                                   summable=bool(m.get("summable", True)))
    goal = raw["goal"]
    economy = raw.get("economy") or {}
    horizon = economy.get("cohort_horizon_days")
    horizon_proof = str(economy.get("cohort_horizon_proof", "")).strip()
    if horizon is not None:
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
            raise ValueError(f"economy.cohort_horizon_days = {horizon!r}: нужно целое число дней больше нуля")
        if not horizon_proof:
            raise ValueError("горизонт когорты без доказательства — нужен перцентиль цикла сделки на зрелых когортах")
    if goal["metric"] not in metrics:
        raise ValueError(f"цель: метрика «{goal['metric']}» не объявлена в metrics")
    return InstanceConfig(
        goal_metric=goal["metric"],
        goal_window_months=int(goal["window_months"]),
        goal_baseline=float(goal["baseline"]),
        target_periods=tuple(goal["target_periods"]),
        business_form=dict(form),
        levels=levels,
        flows=tuple(raw["flows"]),
        metrics=metrics,
        storage_adapter=raw["storage"]["adapter"],
        storage_link_domains=_link_domains(raw["storage"]),
        cohort_horizon_days=horizon,
        cohort_horizon_proof=horizon_proof,
    )
