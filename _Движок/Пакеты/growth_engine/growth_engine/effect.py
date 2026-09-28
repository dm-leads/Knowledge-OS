"""Эффект гипотезы моделью: прирост в единицах цели и в ₽ из живых чисел книги — номера чисел для карточки кандидата.

Зачем (24.09.2026): карточка кандидата требует эффект номерами записанных чисел, а считать деньги в ответе агента нельзя.
До этой команды эффект вписывала приёмочная сцена руками.

Два вида изменения:
- доля (`from`, `to`, `share_metric`): прирост ступени `to` = ступень `from` × изменение доли;
- ступень (`stage`): прирост ступени = ступень × изменение.
Изменение — «абсолютное» (доля в долях единицы, 0.003 = 0,3 п.п.; ступень — в штуках) или «относительное» (0.10 = +10 %).
Прирост переводится в единицы цели по конверсии ниже по воронке ТОЙ ЖЕ единицы действия, окна и съёма; в ₽ — через
деньги на единицу цели кабинета из модели (`effect_rub` ядра, страж 8). Эффект единицы действия — это прирост цели
кабинета, поэтому в ₽ он считается на уровне кабинета; средние деньги кабинета — оговорка, а не молчание.
Всё — «оценка»; база, конверсия и деньги — одного съёма (П3). Пишутся: доля базы (для гейта), эффект в единицах цели,
эффект в ₽; печатаются их номера. Успех — «записано N, прочитано N».

Запуск из Скрипты/:
  py -3 -m growth_engine.effect --config "../Планирование/Движок роста/Конфигурация инстанса.yaml" --json '{...}'
     --google-book <ключ книги>
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import yaml

from .core.arithmetic import ratio, render
from .core.config import parse_config
from .core.economy import effect_rub
from .core.errors import GuardViolation
from .core.number import Number, Status
from .core.storage import number_id
from .funnel_run import parse_months
from .storage.selection import add_store_arguments, open_store

KINDS = ("абсолютное", "относительное")


def _change(fields: dict) -> tuple[float, str]:
    change, kind = fields.get("change"), fields.get("kind")
    if kind not in KINDS or not isinstance(change, (int, float)) or isinstance(change, bool):
        raise GuardViolation(9, f"эффект: нужны «change» числом и «kind» из {' / '.join(KINDS)}, получено "
                                f"change={change!r}, kind={kind!r}")
    return float(change), kind


def _pick(numbers, metric: str, scope: str, flow: str, segment: str, window, take=None) -> Number:
    found = [x for x in numbers if (x.metric, x.scope, x.flow, x.segment, x.period_start, x.period_end)
             == (metric, scope, flow, segment, *window) and (take is None or x.as_of == take)]
    if not found:
        where = f"{scope}, {flow}" + (f", {segment}" if segment else "")
        raise GuardViolation(13, f"эффект: на листе снимков нет «{metric}» ({where}) за {window[0]:%m.%Y}"
                                 + (f", съём {take:%d.%m.%Y}" if take else "") + " — сначала funnel_run")
    return max(found, key=lambda x: x.as_of or date.min)


def compute(numbers, fields: dict, cfg, system: str, today: date) -> dict:
    """Числа эффекта: {'fact_basis', 'share' (или None), 'effect_goal_units', 'effect_rub'}."""
    hypothesis = str(fields.get("hypothesis") or "")
    if not hypothesis:
        raise GuardViolation(9, "эффект: нужен «hypothesis» — номер гипотезы, чей эффект считается")
    scope, flow, segment = fields.get("scope"), fields.get("flow"), fields.get("segment", "")
    window = parse_months(str(fields.get("month", "")))[0]
    change, kind = _change(fields)
    goal = cfg.goal_metric

    if fields.get("stage"):
        moved = _pick(numbers, fields["stage"], scope, flow, segment, window)
        take = moved.as_of
        base_value = moved.value
        delta = base_value * change if kind == "относительное" else change
        fact_basis, share = moved, None
        how = (f"{moved.metric} {base_value:g} × {change:g}" if kind == "относительное"
               else f"{moved.metric} + {change:g}")
    else:
        source_stage = _pick(numbers, fields.get("from"), scope, flow, segment, window)
        take = source_stage.as_of
        moved = _pick(numbers, fields.get("to"), scope, flow, segment, window, take)
        if not fields.get("share_metric"):
            raise GuardViolation(9, "эффект: для изменения доли нужен «share_metric» — имя доли, как у гейта")
        share = ratio(moved, source_stage, fields["share_metric"], cfg)
        if share.value is None:
            raise GuardViolation(13, f"эффект: доля «{share.metric}» — нет данных")
        delta = source_stage.value * (change if kind == "абсолютное" else share.value * change)
        fact_basis = share
        how = (f"{source_stage.metric} {source_stage.value:g} × "
               + (f"{change * 100:g} п.п." if kind == "абсолютное" else f"{share.value:.4f} × {change:g}"))

    target = _pick(numbers, goal, scope, flow, segment, window, take) if moved.metric != goal else moved
    to_goal = 1.0 if moved.metric == goal else (target.value / moved.value if moved.value else None)
    if to_goal is None:
        raise GuardViolation(13, f"эффект: ступень «{moved.metric}» пуста — конверсия до «{goal}» не считается")
    units = delta * to_goal

    money = [x for x in numbers if x.metric == "ampu" and x.scope == scope and x.flow == "all" and not x.segment]
    if not money:
        raise GuardViolation(13, f"эффект: на листе снимков нет денег на «{goal}» кабинета {scope} — сначала economy_run")
    money_per_unit = max(money, key=lambda x: x.as_of or date.min)
    if money_per_unit.as_of != take:
        raise GuardViolation(4, f"эффект: база снята {take:%d.%m.%Y}, деньги на единицу — {money_per_unit.as_of:%d.%m.%Y}; "
                                "разные съёмы не перемножаются (П3) — повторить economy_run или funnel_run")

    basis = (f"гипотеза {hypothesis}: прирост «{moved.metric}» = {how} = {delta:g}; × {goal}/{moved.metric} "
             f"{to_goal:.4f} = {units:g} в месяц; единица действия {scope}, {flow}"
             + (f", {segment}" if segment else "") + f"; окно {window[0]:%m.%Y}; в {goal} кабинета")
    effect = Number(metric=goal, level=cfg.rule(goal).level, scope=scope, flow="all", segment="",
                    period_start=window[0], period_end=window[1], as_of=take,
                    source=f"{money_per_unit.source_class}:{system}:эффект гипотезы {hypothesis}",
                    status=Status.ESTIMATE, value=units, missing=basis)
    rub = effect_rub(effect, money_per_unit)
    rub = replace(rub, source=f"{rub.source_class}:{rub.source_system}:эффект гипотезы {hypothesis} в ₽")
    if share is not None:
        share = replace(share, source=f"{share.source_class}:{share.source_system}:доля гипотезы {hypothesis}")
    return {"fact_basis": fact_basis if share is None else share, "share": share, "effect_goal_units": effect,
            "effect_rub": rub}


def run(args, out=print, today=date.today, bridge_factory=None, google_factory=None) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    fields = yaml.safe_load(args.json) if args.json else None
    if not isinstance(fields, dict):
        raise GuardViolation(9, "эффект: --json — поля расчёта (hypothesis, scope, flow, segment, month, change, kind "
                                "и from/to/share_metric или stage)")
    system = (raw.get("funnel") or {}).get("system") or "модель"
    with open_store(args, cfg.storage_link_domains, today=today, bridge_factory=bridge_factory,
                    google_factory=google_factory) as (store, label):
        result = compute(store.read("numbers"), fields, cfg, system, today())
        written = [result["effect_goal_units"], result["effect_rub"]]
        if result["share"] is not None:
            written.insert(0, result["share"])
        report = store.create_numbers(written)
    out(f"хранилище: {label}")
    for key in ("fact_basis", "effect_goal_units", "effect_rub"):
        out(f"   {key}: {render(result[key])}")
    out(f"fact_basis_id: {number_id(result['fact_basis'])}")
    if result["share"] is not None:
        out(f"share_id (для гейта): {number_id(result['share'])}")
    out(f"effect_goal_units_id: {number_id(result['effect_goal_units'])}")
    out(f"effect_rub_id: {number_id(result['effect_rub'])}")
    out(f"«{report.artifact}»: записано {report.rows_written}, прочитано {report.rows_read_back}")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="growth_engine.effect", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--json", required=True, help="поля расчёта эффекта в JSON")
    add_store_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except GuardViolation as exc:
        print(f"❌ эффект не посчитан: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
