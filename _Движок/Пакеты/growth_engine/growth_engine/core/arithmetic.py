"""Арифметика над каноническими числами: стражи 3 и 4, стандарт ответа (страж 9)."""
from __future__ import annotations

from datetime import timedelta

from .config import InstanceConfig
from .errors import GuardViolation
from .number import STATUS_RANK, Number, Status


def _weakest(statuses):
    return min(statuses, key=lambda s: STATUS_RANK[s])


def add(items: list[Number], cfg: InstanceConfig) -> Number:
    """Сумма одной метрики. Страж 3: разные ступени, потоки, источники, кабинеты и окна
    складываются только по правилам, объявленным в конфигурации инстанса."""
    if len(items) < 2:
        raise ValueError("для суммы нужно минимум два числа")
    first = items[0]
    rule = cfg.rule(first.metric)
    if not rule.summable:
        raise GuardViolation(3, f"«{first.metric}» — среднее или доля, не складывается")
    for x in items:
        if x.level != cfg.rule(x.metric).level:
            raise GuardViolation(3, f"«{x.metric}»: уровень «{x.level}» не совпадает с объявленным в конфигурации")
    for x in items[1:]:
        if x.metric != first.metric or x.level != first.level:
            raise GuardViolation(3, f"складываются разные ступени: «{first.metric}» и «{x.metric}»")
        if x.flow != first.flow:
            raise GuardViolation(3, f"складываются разные потоки: «{first.flow}» и «{x.flow}»")
        if x.source != first.source:
            raise GuardViolation(3, "складываются числа разных классов, систем-источников или запросов "
                                    f"({first.source} / {x.source})")
        if x.unit != first.unit or x.unit == "доля":
            raise GuardViolation(3, "доли и величины в разных единицах не складываются")
        if x.as_of != first.as_of:
            raise GuardViolation(3, f"складываются числа разных съёмов ({first.as_of} и {x.as_of}): "
                                    "данные меняются задним числом (Т1, Т6)")
    segments = sorted({x.segment for x in items})
    if len(segments) > 1 and ("" in segments or len({s.partition("=")[0] for s in segments}) != 1):
        raise GuardViolation(3, "складываются итог и его часть или разрезы разных измерений — двойной счёт")
    scopes = sorted({x.scope for x in items})
    periods = sorted({(x.period_start, x.period_end) for x in items})
    keys = [(x.scope, x.segment, x.period_start, x.period_end) for x in items]
    if len(set(keys)) != len(keys):
        raise GuardViolation(3, "одно и то же число передано дважды — двойной счёт")
    if len(scopes) > 1 and not rule.cross_scope_additive:
        raise GuardViolation(3, f"«{first.metric}» разных кабинетов не складывается: сквозной дедуп не доказан")
    if len(periods) > 1:
        if not rule.period_additive:
            raise GuardViolation(3, f"«{first.metric}» разных окон не складывается: аддитивность по периоду не объявлена")
        for (_, end), (start, _) in zip(periods, periods[1:]):
            if end != start:
                raise GuardViolation(3, "окна должны идти встык — без пересечений и разрывов")
    if len(keys) != len(scopes) * len(periods) * len(segments):
        raise GuardViolation(3, "неполная сетка «кабинет × разрез × окно» — сумма не имеет смысла")
    status = _weakest([x.status for x in items])
    value = None if status is Status.NO_DATA else sum(x.value for x in items)
    segment = segments[0] if len(segments) == 1 else segments[0].partition("=")[0] + f"=сумма {len(segments)} значений"
    return Number(metric=first.metric, level=first.level, scope="+".join(scopes), flow=first.flow,
                  segment=segment,
                  period_start=periods[0][0], period_end=periods[-1][1], as_of=first.as_of,
                  source=f"{first.source_class}:{first.source_system}:сумма",
                  status=status, value=value, unit=first.unit,
                  missing="; ".join(sorted({x.missing for x in items if x.missing})))


def _plain(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v}"


def ratio(numerator: Number, denominator: Number, metric: str, cfg: InstanceConfig) -> Number:
    """Конверсия одной природы. Страж 4: период, кабинет, поток, класс и система-источник совпадают;
    обе величины — счётчики; числитель не выше знаменателя по воронке. Иначе метрика смешанная."""
    pairs = (
        ("период", (numerator.period_start, numerator.period_end),
         (denominator.period_start, denominator.period_end)),
        ("кабинет", numerator.scope, denominator.scope),
        ("поток", numerator.flow, denominator.flow),
        ("источник", numerator.source, denominator.source),
        ("единица", numerator.unit, denominator.unit),
        ("дата съёма", numerator.as_of, denominator.as_of),
    )
    for name, a, b in pairs:
        if a != b:
            raise GuardViolation(4, f"смешанная метрика «{metric}»: не совпадает {name} ({a} / {b})")
    # Сегменты совпадают, либо это доля сегмента в итоге той же метрики (структура, а не смешение).
    share_of_total = (numerator.metric == denominator.metric and not denominator.segment
                      and bool(numerator.segment))
    if numerator.segment != denominator.segment and not share_of_total:
        raise GuardViolation(4, f"смешанная метрика «{metric}»: не совпадает сегмент "
                                f"({numerator.segment or 'итог'} / {denominator.segment or 'итог'})")
    if numerator.unit != "шт":
        raise GuardViolation(4, f"«{metric}»: конверсия считается только из счётчиков, не из «{numerator.unit}»")
    for x in (numerator, denominator):
        if x.level != cfg.rule(x.metric).level:
            raise GuardViolation(4, f"«{metric}»: уровень «{x.level}» у «{x.metric}» не совпадает с объявленным в конфигурации")
    if numerator.level not in cfg.levels or denominator.level not in cfg.levels:
        raise GuardViolation(4, f"«{metric}»: уровни «{numerator.level}» и «{denominator.level}» не объявлены")
    if cfg.levels.index(numerator.level) < cfg.levels.index(denominator.level):
        raise GuardViolation(4, f"«{metric}»: числитель «{numerator.level}» выше знаменателя «{denominator.level}» по воронке")
    common = dict(metric=metric, level=f"{numerator.level}/{denominator.level}", scope=numerator.scope,
                  flow=numerator.flow, segment=numerator.segment, period_start=numerator.period_start,
                  period_end=numerator.period_end, source=numerator.source, unit="доля",
                  as_of=numerator.as_of)
    status = _weakest([numerator.status, denominator.status])
    notes = [note for note in dict.fromkeys((numerator.missing, denominator.missing)) if note]
    if status is Status.NO_DATA or not denominator.value:
        return Number(**common, status=Status.NO_DATA, value=None, denominator=denominator.metric,
                      missing="; ".join(notes + ["знаменатель пуст или нет данных"]))
    return Number(**common, status=status, value=numerator.value / denominator.value,
                  denominator=f"{denominator.metric}={_plain(denominator.value)}", missing="; ".join(notes))


def render(x: Number) -> str:
    """Стандарт ответа о числе (страж 9): значение · период · знаменатель · источник · статус · чего не хватает."""
    if x.value is None:
        value = "нет данных"
    elif x.unit == "доля":
        value = f"{x.value * 100:.2f}%".replace(".", ",")
    else:
        value = f"{x.value:,.0f}".replace(",", " ")
    last_day = x.period_end - timedelta(days=1)
    parts = [f"{x.metric} = {value}", f"кабинет {x.scope}"]
    if x.segment:
        parts.append(f"разрез {x.segment}")
    parts += [f"период {x.period_start:%d.%m.%Y}–{last_day:%d.%m.%Y}",
              f"знаменатель {x.denominator or '—'}",
              f"источник {x.source}",
              f"снято {x.as_of:%d.%m.%Y}",
              f"статус {x.status.value}"]
    if x.missing:
        parts.append(f"не хватает: {x.missing}")
    return " · ".join(parts)
