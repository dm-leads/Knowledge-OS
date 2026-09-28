"""Движок экономики (слой 3): сверка систем, деньги на единицу, маржа, тождество PnL, дозревание окна, денежная модель,
порог шума, окно цели, разложение доли на структуру и конверсию.

Стражи: 2 — кросс-чек модели и сверка систем; 4 — смешанная метрика; 8 — эффект в ₽ только отсюда.
Модуль не знает имён систем, кабинетов и полей источников: всё приходит каноническими числами и параметрами.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

from .arithmetic import add, ratio
from .config import InstanceConfig
from .errors import GuardViolation
from .number import STATUS_RANK, Number, Status
from .registry import MODEL_SYSTEM

NOT_MONEY_UNITS = ("шт", "доля")


@dataclass(frozen=True)
class Tolerance:
    """Допуск сверки систем: стоп, только когда превышены оба порога — абсолютный и относительный."""
    abs_units: float
    rel: float

    def __post_init__(self):
        if self.abs_units < 0 or self.rel < 0:
            raise ValueError("допуск сверки не может быть отрицательным")


@dataclass(frozen=True)
class Reconciliation:
    left: Number
    right: Number
    diff: float | None        # правая система минус левая
    rel: float | None         # |diff| / левая
    ok: bool
    note: str


def _plain(x: Number) -> str:
    return "нет данных" if x.value is None else f"{x.value:g}"


def _integer_or_float(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value}"


def _percent(share: float) -> str:
    return f"{share:.1%}".replace(".", ",")


def _weakest(*statuses: Status) -> Status:
    return min(statuses, key=lambda status: STATUS_RANK[status])


def _notes(*numbers: Number) -> str:
    return "; ".join(dict.fromkeys(x.missing for x in numbers if x.missing))


def _is_money(x: Number) -> bool:
    return x.unit not in NOT_MONEY_UNITS and " на " not in x.unit


def _same_nature(a: Number, b: Number, metric: str) -> None:
    """Страж 4: окно, кабинет, поток, разрез, источник и дата съёма совпадают — иначе величина смешанная."""
    pairs = (
        ("окно", (a.period_start, a.period_end), (b.period_start, b.period_end)),
        ("кабинет", a.scope, b.scope),
        ("поток", a.flow, b.flow),
        ("разрез", a.segment, b.segment),
        ("источник", a.source, b.source),
        ("дата съёма", a.as_of, b.as_of),
    )
    for name, x, y in pairs:
        if x != y:
            raise GuardViolation(4, f"«{metric}»: смешанная величина — не совпадает {name} ({x} / {y})")


def _declared_levels(metric: str, cfg: InstanceConfig, *numbers: Number) -> None:
    for x in numbers:
        if x.level != cfg.rule(x.metric).level:
            raise GuardViolation(4, f"«{metric}»: уровень «{x.level}» у «{x.metric}» не совпадает с конфигурацией")


def reconcile(left: Number, right: Number, tolerance: Tolerance, max_fetch_gap_days: int = 0) -> Reconciliation:
    """Сверка одной величины из двух систем — мост между системами, а не деление (П2). Сравнение явное, поэтому
    разные даты съёма допустимы, но только в объявленных пределах (П3)."""
    pairs = (
        ("метрика", left.metric, right.metric),
        ("кабинет", left.scope, right.scope),
        ("окно", (left.period_start, left.period_end), (right.period_start, right.period_end)),
        ("поток", left.flow, right.flow),
        ("разрез", left.segment, right.segment),
        ("единица", left.unit, right.unit),
    )
    for name, a, b in pairs:
        if a != b:
            raise GuardViolation(4, f"сверяются разные величины: не совпадает {name} ({a} / {b})")
    if left.source_system == right.source_system:
        raise GuardViolation(4, f"сверка внутри одной системы «{left.source_system}» — не мост между системами")
    gap = abs((left.as_of - right.as_of).days)
    if gap > max_fetch_gap_days:
        raise GuardViolation(4, f"съёмы разнесены на {gap} дн. при допустимых {max_fetch_gap_days}: "
                                f"{left.as_of:%d.%m.%Y} и {right.as_of:%d.%m.%Y}")
    last_day = left.period_end - timedelta(days=1)
    head = (f"{left.metric} · кабинет {left.scope} · окно {left.period_start:%d.%m.%Y}–{last_day:%d.%m.%Y}: "
            f"{left.source_system} {_plain(left)} против {right.source_system} {_plain(right)}")
    if left.value is None or right.value is None:
        return Reconciliation(left, right, None, None, False, f"{head} — нет данных для сверки")
    diff = right.value - left.value
    rel = abs(diff) / abs(left.value) if left.value else (0.0 if diff == 0 else math.inf)
    ok = not (abs(diff) > tolerance.abs_units and rel > tolerance.rel)
    return Reconciliation(left, right, diff, rel, ok,
                          f"{head} — расхождение {diff:+g} ({_percent(rel)}), {'в допуске' if ok else 'вне допуска'} "
                          f"(допуск {tolerance.abs_units:g} шт. или {_percent(tolerance.rel)})")


def require_reconciled(results: list[Reconciliation]) -> None:
    """Страж 2: сверка систем не прошла хотя бы в одном окне — модель по этим данным не считается."""
    failed = [result for result in results if not result.ok]
    if failed:
        raise GuardViolation(2, "сверка систем не прошла: " + "; ".join(result.note for result in failed))


def per_unit(money: Number, count: Number, metric: str, cfg: InstanceConfig) -> Number:
    """Деньги на единицу счётчика той же системы, окна, кабинета, потока, разреза и съёма (страж 4, П2, П3)."""
    _same_nature(money, count, metric)
    if not _is_money(money):
        raise GuardViolation(4, f"«{metric}»: делимое «{money.metric}» в «{money.unit}» — не деньги")
    if count.unit != "шт":
        raise GuardViolation(4, f"«{metric}»: деньги делятся только на счётчик, а не на «{count.unit}»")
    _declared_levels(metric, cfg, money, count)
    common = dict(metric=metric, level=money.level, scope=money.scope, flow=money.flow, segment=money.segment,
                  period_start=money.period_start, period_end=money.period_end, as_of=money.as_of,
                  source=money.source, unit=f"{money.unit} на {count.metric}")
    status = _weakest(money.status, count.status)
    if status is Status.NO_DATA or not count.value:
        return Number(**common, status=Status.NO_DATA, value=None, denominator=count.metric,
                      missing="; ".join(filter(None, [_notes(money, count), "знаменатель пуст или нет данных"])))
    return Number(**common, status=status, value=money.value / count.value,
                  denominator=f"{count.metric}={_integer_or_float(count.value)}", missing=_notes(money, count))


def money_share(part: Number, whole: Number, metric: str, cfg: InstanceConfig) -> Number:
    """Доля одних денег в других той же системы, окна, кабинета и съёма — например, маржа (страж 4)."""
    _same_nature(part, whole, metric)
    if not (_is_money(part) and _is_money(whole)) or part.unit != whole.unit:
        raise GuardViolation(4, f"«{metric}»: доля считается из денег одной валюты — «{part.unit}» / «{whole.unit}»")
    _declared_levels(metric, cfg, part, whole)
    common = dict(metric=metric, level=f"{part.level}/{whole.level}", scope=part.scope, flow=part.flow,
                  segment=part.segment, period_start=part.period_start, period_end=part.period_end, as_of=part.as_of,
                  source=part.source, unit="доля")
    status = _weakest(part.status, whole.status)
    if status is Status.NO_DATA or not whole.value:
        return Number(**common, status=Status.NO_DATA, value=None, denominator=whole.metric,
                      missing="; ".join(filter(None, [_notes(part, whole), "знаменатель пуст или нет данных"])))
    return Number(**common, status=status, value=part.value / whole.value,
                  denominator=f"{whole.metric}={_integer_or_float(whole.value)} {whole.unit}", missing=_notes(part, whole))


def check_pnl(revenue: Number, cogs: Number, profit: Number, tolerance: float = 1.0) -> None:
    """Страж 2: валовая прибыль двумя путями — полем прибыли и выручкой минус себестоимость."""
    for x in (cogs, profit):
        _same_nature(revenue, x, "тождество PnL")
    if len({revenue.unit, cogs.unit, profit.unit}) != 1 or not _is_money(revenue):
        raise GuardViolation(4, "тождество PnL: выручка, себестоимость и прибыль — деньги одной валюты")
    if any(x.value is None for x in (revenue, cogs, profit)):
        raise GuardViolation(2, f"тождество выручка − себестоимость = прибыль не проверить: нет данных "
                                f"(кабинет {revenue.scope})")
    gap = revenue.value - cogs.value - profit.value
    if abs(gap) > tolerance:
        last_day = revenue.period_end - timedelta(days=1)
        raise GuardViolation(2, f"выручка − себестоимость − прибыль = {gap:+,.0f} {revenue.unit}".replace(",", " ")
                                + f" при допуске {tolerance:g}: кабинет {revenue.scope}, окно "
                                  f"{revenue.period_start:%d.%m.%Y}–{last_day:%d.%m.%Y} — деньги модели не сходятся")


def mature(x: Number, horizon_days: int) -> Number:
    """Т6: окно моложе цикла сделки дозревает — статус не выше «оценки», дата дозревания в оговорке."""
    matures_on = x.period_end + timedelta(days=horizon_days)
    if x.status is Status.NO_DATA or matures_on <= x.as_of:
        return x
    note = f"окно дозревает до {matures_on:%d.%m.%Y} (Т6)"
    return replace(x, status=_weakest(x.status, Status.ESTIMATE), missing="; ".join(filter(None, [x.missing, note])))


@dataclass(frozen=True)
class MoneyModel:
    """Юнит-экономика одной системы, окна, кабинета и съёма (П2). AMPU = C1 × чек × маржа выполняется по построению,
    поэтому кросс-чеком не считается; кросс-чек — тождество PnL."""
    users: Number
    sales: Number
    revenue: Number
    cogs: Number
    profit: Number
    c1: Number
    avg_check: Number
    margin: Number
    amppu: Number
    ampu: Number


def money_model(users: Number, sales: Number, revenue: Number, cogs: Number, profit: Number, cfg: InstanceConfig,
                pnl_tolerance: float = 1.0) -> MoneyModel:
    """Денежная модель кабинета: C1 на оплаченную сделку, средний чек, маржа, AMPPU на оплату, AMPU на New First SQL.
    Оплаты и деньги окна моложе горизонта конфигурации дозревают (Т6); вход окна задним числом не меняется."""
    check_pnl(revenue, cogs, profit, pnl_tolerance)
    if cfg.cohort_horizon_days:
        sales, revenue, cogs, profit = (mature(x, cfg.cohort_horizon_days) for x in (sales, revenue, cogs, profit))
    return MoneyModel(users=users, sales=sales, revenue=revenue, cogs=cogs, profit=profit,
                      c1=ratio(sales, users, "c1", cfg), avg_check=per_unit(revenue, sales, "avg_check", cfg),
                      margin=money_share(profit, revenue, "margin", cfg), amppu=per_unit(profit, sales, "amppu", cfg),
                      ampu=per_unit(profit, users, "ampu", cfg))


NOISE_MIN_EXPECTED = 5   # нормальное приближение порога шума: N·p и N·(1−p) не меньше 5
_COUNT_BASE = re.compile(r"[^=]+=(\d+)")


def noise(share: Number, base: int | None = None) -> Number:
    """Порог шума доли: ±2·√(p(1−p)/N). N — база доли или, при запуске теста, ожидаемый N теста (`base`).
    Порог определён для доли счётчиков при N·p ≥ 5 и N·(1−p) ≥ 5: при p = 0 или 1 формула дала бы нулевой шум, при
    малых N·p нормальное приближение неверно. Ожидаемый эффект меньше порога — research, а не тест."""
    if share.unit != "доля":
        raise GuardViolation(9, f"порог шума считается для доли, а «{share.metric}» — в «{share.unit}»")
    if base is not None and (isinstance(base, bool) or not isinstance(base, int) or base <= 0):
        raise GuardViolation(9, f"порог шума: N = {base!r} — нужно целое больше нуля")
    denominator = share.denominator or share.metric
    counted = _COUNT_BASE.fullmatch(denominator)
    if "=" in denominator and not counted:
        raise GuardViolation(9, f"порог шума считается для доли счётчиков, а база «{denominator}» — не число наблюдений")
    observations = base if base is not None else (int(counted.group(1)) if counted else 0)
    common = dict(metric=f"{share.metric}: порог шума", level=share.level, scope=share.scope, flow=share.flow,
                  segment=share.segment, period_start=share.period_start, period_end=share.period_end,
                  as_of=share.as_of, source=share.source, unit="доля",
                  denominator=denominator if base is None else f"ожидаемый N теста={base}")
    if share.value is None or observations <= 0:
        return Number(**common, status=Status.NO_DATA, value=None, missing="у доли нет значения или базы")
    if not 0 <= share.value <= 1:
        return Number(**common, status=Status.NO_DATA, value=None,
                      missing=f"доля {share.value:g} вне [0; 1] — не биномиальная доля, порог шума не определён")
    p = share.value
    if observations * p < NOISE_MIN_EXPECTED or observations * (1 - p) < NOISE_MIN_EXPECTED:
        return Number(**common, status=Status.NO_DATA, value=None,
                      missing=f"N·p = {observations * p:g}, N·(1−p) = {observations * (1 - p):g} — меньше "
                              f"{NOISE_MIN_EXPECTED}: нормальное приближение неприменимо, порог шума не определён")
    return Number(**common, status=share.status, value=2 * math.sqrt(p * (1 - p) / observations),
                  missing=share.missing)


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def _goal_window(months: list[Number], cfg: InstanceConfig, expected: int) -> Number:
    if cfg.goal_window_months < 2:
        raise GuardViolation(9, "окно цели короче двух месяцев — считать нечего")
    if len(months) != expected:
        raise GuardViolation(9, f"окно цели — {cfg.goal_window_months} мес.: нужно {expected} месячных чисел, "
                                f"передано {len(months)}")
    for x in months:
        if x.metric != cfg.goal_metric:
            raise GuardViolation(9, f"окно считается по цели «{cfg.goal_metric}», а не по «{x.metric}»")
        if x.period_start.day != 1 or x.period_end != _next_month(x.period_start):
            raise GuardViolation(9, f"окно цели складывается из календарных месяцев, а не из "
                                    f"{x.period_start}–{x.period_end}")
    return add(months, cfg) if len(months) > 1 else months[0]


def window_average(months: list[Number], cfg: InstanceConfig) -> Number:
    """Цель — среднее окна: N = (M−2 + M−1 + M) / k. Месяцы — встык и одного съёма (страж 3, П3)."""
    k = cfg.goal_window_months
    total = _goal_window(months, cfg, k)
    return replace(total, metric=f"{total.metric}: среднее окна {k} мес.", unit="шт в месяц",
                   value=None if total.value is None else total.value / k)


def required_last_month(previous: list[Number], target: float, cfg: InstanceConfig) -> Number:
    """Сколько цели нужно в следующем месяце, чтобы окно дало target в месяц: M = k × target − сумма прежних месяцев."""
    k = cfg.goal_window_months
    total = _goal_window(previous, cfg, k - 1)
    start = total.period_end
    common = dict(metric=f"{total.metric}: требуется в следующем месяце", level=total.level, scope=total.scope,
                  flow=total.flow, period_start=start, period_end=_next_month(start), as_of=total.as_of,
                  source=total.source)
    note = f"чтобы окно {k} мес. дало {target:g} в месяц — требуемое, не факт"
    if total.value is None:
        return Number(**common, status=Status.NO_DATA, value=None, missing=note)
    return Number(**common, status=_weakest(total.status, Status.ESTIMATE), value=k * target - total.value,
                  missing="; ".join(filter(None, [total.missing, note])))


@dataclass(frozen=True)
class ShiftShare:
    """Изменение доли «события / база» между двумя окнами: сдвиг структуры сегментов и конверсия внутри них."""
    structure: Number
    within: Number
    total: Number


def shift_share(base_before: list[Number], events_before: list[Number], base_after: list[Number],
                events_after: list[Number], cfg: InstanceConfig, tolerance: float = 1e-9) -> ShiftShare:
    """Страж 4: вклад структуры (при прежней конверсии) + вклад конверсии (при новой структуре) обязаны давать
    изменение доли по тем же сегментам. Сегменты — с ненулевой базой в обоих окнах, числа — одного съёма."""
    sides = []
    for label, base, events in (("первого окна", base_before, events_before), ("второго окна", base_after, events_after)):
        by_base, by_events = {x.segment: x for x in base}, {x.segment: x for x in events}
        if (not base or len(by_base) != len(base) or len(by_events) != len(events) or set(by_base) != set(by_events)
                or "" in by_base):
            raise GuardViolation(4, f"разложение: сегменты базы и событий {label} не совпадают, повторяются или пусты")
        for segment, x in by_base.items():
            ratio(by_events[segment], x, "доля сегмента", cfg)
        sides.append((by_base, by_events))
    (base_b, events_b), (base_a, events_a) = sides
    if set(base_b) != set(base_a):
        raise GuardViolation(4, "разложение: сегменты окон не совпадают — берутся сегменты с базой в обоих окнах")
    numbers = [*base_b.values(), *events_b.values(), *base_a.values(), *events_a.values()]
    if len({x.as_of for x in numbers}) != 1:
        raise GuardViolation(4, "разложение: числа разных съёмов не сравниваются (П3)")
    if len({(x.scope, x.flow, x.source) for x in numbers}) != 1:
        raise GuardViolation(4, "разложение: окна из разных кабинетов, потоков, источников или запросов")
    if (len({x.metric for x in (*base_b.values(), *base_a.values())}) != 1
            or len({x.metric for x in (*events_b.values(), *events_a.values())}) != 1):
        raise GuardViolation(4, "разложение: у сегментов разные метрики базы или событий — конверсии несопоставимы")
    first_b, first_a = next(iter(base_b.values())), next(iter(base_a.values()))
    if (first_b.period_start, first_b.period_end) == (first_a.period_start, first_a.period_end):
        raise GuardViolation(4, "разложение сравнивает два разных окна, а передано одно")
    if any(x.value is None for x in numbers):
        raise GuardViolation(9, "разложение: у сегмента нет данных")
    if any(not x.value for x in (*base_b.values(), *base_a.values())):
        raise GuardViolation(4, "разложение: у сегмента нулевая база — доля не определена; исключите сегмент в обоих окнах")
    total_b, total_a = sum(x.value for x in base_b.values()), sum(x.value for x in base_a.values())
    structure = within = 0.0
    for segment in base_b:
        rate_b = events_b[segment].value / base_b[segment].value
        rate_a = events_a[segment].value / base_a[segment].value
        share_b, share_a = base_b[segment].value / total_b, base_a[segment].value / total_a
        structure += (share_a - share_b) * rate_b
        within += share_a * (rate_a - rate_b)
    change = sum(x.value for x in events_a.values()) / total_a - sum(x.value for x in events_b.values()) / total_b
    if abs(structure + within - change) > tolerance:
        raise GuardViolation(4, f"тождество разложения не сходится: структура {structure:+.6f} + конверсия "
                                f"{within:+.6f} ≠ изменение {change:+.6f}")
    first_events = next(iter(events_b.values()))
    name = f"{first_events.metric}/{first_b.metric}"
    common = dict(level=f"{first_events.level}/{first_b.level}", scope=first_b.scope, flow=first_b.flow,
                  period_start=min(first_b.period_start, first_a.period_start),
                  period_end=max(first_b.period_end, first_a.period_end), as_of=first_b.as_of, source=first_b.source,
                  unit="доля", denominator=f"{first_a.metric}={_integer_or_float(total_a)}",
                  status=_weakest(*(x.status for x in numbers)),
                  missing=f"сравнение окон {first_b.period_start:%m.%Y} и {first_a.period_start:%m.%Y}; "
                          f"сегментов: {len(base_b)}")
    return ShiftShare(structure=Number(metric=f"{name}: вклад структуры", value=structure, **common),
                      within=Number(metric=f"{name}: вклад конверсии", value=within, **common),
                      total=Number(metric=f"{name}: изменение доли", value=change, **common))


def effect_rub(effect: Number, money_per_unit: Number, metric: str = "effect_rub") -> Number:
    """Страж 8: эффект гипотезы в ₽ — единицы эффекта × деньги на ту же единицу той же системы, кабинета, потока и
    разреза (П2). Эффект — оценка, поэтому итог не сильнее «оценки»; источник итога — модель."""
    if effect.unit != "шт":
        raise GuardViolation(4, f"«{metric}»: эффект — счётчик единиц, а не «{effect.unit}»")
    money_unit, _, per = money_per_unit.unit.partition(" на ")
    if not per or money_unit in NOT_MONEY_UNITS:
        raise GuardViolation(4, f"«{metric}»: множитель «{money_per_unit.metric}» в «{money_per_unit.unit}» — не деньги "
                                "на единицу")
    if per != effect.metric:
        raise GuardViolation(4, f"«{metric}»: эффект в «{effect.metric}» не умножается на деньги на «{per}»")
    if (effect.source_class, effect.source_system) != (money_per_unit.source_class, money_per_unit.source_system):
        raise GuardViolation(4, f"«{metric}»: эффект из «{effect.source_system}», деньги на единицу из "
                                f"«{money_per_unit.source_system}» — разные системы (П2)")
    if (effect.scope, effect.flow, effect.segment) != (money_per_unit.scope, money_per_unit.flow, money_per_unit.segment):
        raise GuardViolation(4, f"«{metric}»: эффект и деньги на единицу — разные кабинеты, потоки или разрезы")
    last_day = money_per_unit.period_end - timedelta(days=1)
    amount = "нет данных" if effect.value is None else _integer_or_float(effect.value)
    basis = (f"{amount} {effect.metric} × {money_per_unit.metric} окна {money_per_unit.period_start:%d.%m.%Y}–"
             f"{last_day:%d.%m.%Y}, снято {money_per_unit.as_of:%d.%m.%Y}")
    common = dict(metric=metric, level=money_per_unit.level, scope=effect.scope, flow=effect.flow,
                  segment=effect.segment, period_start=effect.period_start, period_end=effect.period_end,
                  as_of=effect.as_of, source=f"{money_per_unit.source_class}:{MODEL_SYSTEM}:{metric}", unit=money_unit)
    missing = "; ".join(filter(None, [basis, _notes(effect, money_per_unit)]))
    status = _weakest(effect.status, money_per_unit.status, Status.ESTIMATE)
    if status is Status.NO_DATA or effect.value is None or money_per_unit.value is None:
        return Number(**common, status=Status.NO_DATA, value=None, missing=missing)
    return Number(**common, status=status, value=effect.value * money_per_unit.value, missing=missing)
