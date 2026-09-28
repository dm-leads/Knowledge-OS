"""Цель окна и потолки веток дерева цели — числами на лист «Модель — снимки» (шаг 3).

Зачем (28.09.2026): подкоманда `tree` принимает цель и потолки только номерами записанных чисел, а считать их было
нечем — потолок вписывался бы руками мимо стражей 1 и 4. Команда снимает цель по веткам из системы цели, проверяет,
что ветки делят итог без остатка, и пишет:
- помесячные числа веток по кабинетам и их сумму по кабинетам цели — факт системы-источника;
- цель окна — число модели со статусом «оценка»: это задание владельца, а не замер;
- потолок каждой ветки — число модели, «оценка».

Определение потолка (одно для всех веток-потоков): **лучший месяц ветки в окне истории** — сколько единиц цели ветка
уже давала. Сезон окна цели не учитывается, потолки разных веток взяты из разных месяцев и могут пересекаться — поэтому
разрыв дерева не сильнее «оценки» (ядро, `GoalTree.gap`). Ветка-рычаг «конверсия» — не поток, а прирост: сколько
единиц цели дал бы веб-поток последнего месяца истории, если бы его конверсия «визит → цель» вернулась к лучшему
месяцу окна; считается по каждому кабинету и складывается.

Ветки — раздел `goal_tree` конфигурации инстанса: разрез системы, образцы значений разреза (`fnmatch`) и поток ветки;
в каждом потоке может быть ветка «остальное» (`rest: true`). Значение разреза, не попавшее ни в одну ветку при потоке
без «остального», — стоп: иначе ветки молча не делили бы итог.

Запуск из Скрипты/:
  py -3 -m growth_engine.goal_tree --config "../Планирование/Движок роста/Конфигурация инстанса.yaml"
     --secrets "<файл секретов>" --month 2026-10 --google-book <ключ книги>
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from . import adapters as registry
from .core.arithmetic import add, render
from .core.artifacts import Branch, GoalTree
from .core.config import parse_config
from .core.errors import GuardViolation
from .core.number import Number, Status
from .core.storage import number_id
from .funnel_run import parse_months
from .sources.base import Query, SourceError
from .storage.selection import add_store_arguments, open_store

MODEL_SYSTEM = "модель"
SEGMENT = "ветка дерева цели"
LEVERS = ("conversion",)


@dataclass(frozen=True)
class BranchSpec:
    id: str
    name: str
    flow: str
    patterns: tuple[str, ...] = ()
    rest: bool = False
    lever: str = ""


def parse_branches(section: dict, flows) -> list[BranchSpec]:
    """Ветки из конфигурации: номера уникальны, поток объявлен, «остальное» — не больше одного на поток."""
    specs, seen, rests = [], set(), set()
    for item in section.get("branches") or ():
        spec = BranchSpec(id=str(item.get("id") or ""), name=str(item.get("name") or ""),
                          flow=str(item.get("flow") or ("web" if item.get("lever") else "")),
                          patterns=tuple(str(p) for p in item.get("markers") or ()),
                          rest=bool(item.get("rest", False)), lever=str(item.get("lever") or ""))
        if not spec.id or not spec.name:
            raise GuardViolation(9, "ветка дерева цели: нужны «id» и «name»")
        if spec.id in seen:
            raise GuardViolation(9, f"ветка {spec.id} описана дважды")
        seen.add(spec.id)
        if spec.flow not in flows:
            raise GuardViolation(9, f"ветка {spec.id}: поток «{spec.flow}» не объявлен в flows ({', '.join(flows)})")
        if spec.lever and spec.lever not in LEVERS:
            raise GuardViolation(9, f"ветка {spec.id}: рычаг «{spec.lever}» не из {', '.join(LEVERS)}")
        if not spec.lever and not spec.rest and not spec.patterns:
            raise GuardViolation(9, f"ветка {spec.id}: нужны «markers», «rest: true» или «lever»")
        if spec.rest:
            if spec.flow in rests:
                raise GuardViolation(9, f"поток «{spec.flow}»: ветка «остальное» объявлена дважды")
            rests.add(spec.flow)
        specs.append(spec)
    if not [s for s in specs if not s.lever]:
        raise GuardViolation(9, "дерево цели: нет ни одной ветки-потока")
    return specs


def assign(segments, specs: list[BranchSpec], flow: str) -> dict[str, float]:
    """Значения разреза одного потока → сумма по веткам. Непопавшее без «остального» — стоп."""
    flowing = [s for s in specs if s.flow == flow and not s.lever]
    rest = next((s for s in flowing if s.rest), None)
    sums = {s.id: 0.0 for s in flowing}
    lost = []
    for number in segments:
        value = number.segment.partition("=")[2]
        hit = [s for s in flowing if any(fnmatchcase(value, p) for p in s.patterns)]
        if len(hit) > 1:
            raise GuardViolation(13, f"значение разреза «{value}» попало в ветки {', '.join(s.id for s in hit)} — "
                                     "ветки обязаны не пересекаться")
        target = hit[0] if hit else rest
        if target is None:
            lost.append(value)
            continue
        sums[target.id] += number.value or 0.0
    if lost:
        raise GuardViolation(13, f"поток «{flow}»: значения разреза не попали ни в одну ветку: {', '.join(lost[:10])}"
                                 + (" …" if len(lost) > 10 else "") + " — добавить образец или ветку «остальное»")
    return sums


def _branch_number(spec: BranchSpec, template: Number, value: float) -> Number:
    return Number(metric=template.metric, level=template.level, scope=template.scope, flow=spec.flow,
                  segment=f"{SEGMENT}={spec.id}", period_start=template.period_start, period_end=template.period_end,
                  as_of=template.as_of, source=template.source, status=Status.FACT, value=value, unit=template.unit)


def collect(adapter, specs, goal_metric: str, scopes, windows, breakdown: str, as_of: date):
    """Помесячные числа веток по кабинетам + веб-визиты и веб-цель для рычага конверсии."""
    facts, web = [], {}
    flows = sorted({s.flow for s in specs if not s.lever})
    for start, end in windows:
        for scope in scopes:
            query = dict(scope=scope, period_start=start, period_end=end, as_of=as_of)
            total = adapter.fetch(Query(metric=goal_metric, flow="all", breakdown=None, **query))[0]
            if total.value is None:
                raise GuardViolation(13, f"{start:%m.%Y} · {scope}: у цели нет данных — {total.missing}")
            parts = 0.0
            for flow in flows:
                segments = adapter.fetch(Query(metric=goal_metric, flow=flow, breakdown=breakdown, **query))
                sums = assign([x for x in segments if x.value is not None], specs, flow)
                for spec in specs:
                    if spec.id in sums:
                        facts.append(_branch_number(spec, total, sums[spec.id]))
                        parts += sums[spec.id]
            if abs(parts - total.value) > 1e-9:
                raise GuardViolation(13, f"{start:%m.%Y} · {scope}: ветки дают {parts:g}, а итог цели {total.value:g} — "
                                         "ветки не делят итог без остатка")
            if any(s.lever == "conversion" for s in specs):
                visits = adapter.fetch(Query(metric="visits", flow="web", breakdown=None, **query))[0]
                goal_web = adapter.fetch(Query(metric=goal_metric, flow="web", breakdown=None, **query))[0]
                web[(scope, start)] = (visits, goal_web)
    return facts, web


def _month(number: Number) -> str:
    return f"{number.period_start:%m.%Y}"


def build(facts, web, specs, cfg, scopes, windows, target: float, target_note: str, month, as_of: date, system: str):
    """Суммы по кабинетам цели, цель окна и потолки веток. Возвращает (суммы, цель, {ветка: потолок})."""
    combined = []
    for spec in [s for s in specs if not s.lever]:
        for start, _ in windows:
            items = [x for x in facts if x.segment == f"{SEGMENT}={spec.id}" and x.period_start == start]
            combined.append(add(items, cfg) if len(items) > 1 else items[0])
    first = combined[0]
    source_class = first.source_class
    scope = first.scope
    common = dict(metric=first.metric, level=first.level, scope=scope, period_start=month[0], period_end=month[1],
                  as_of=as_of, unit=first.unit)
    goal = Number(**common, flow="all", segment="", source=f"{source_class}:{MODEL_SYSTEM}:цель окна",
                  status=Status.ESTIMATE, value=float(target), missing=target_note or "цель владельца, не замер")
    history = f"{windows[0][0]:%m.%Y}–{windows[-1][0]:%m.%Y}"
    ceilings = {}
    for spec in specs:
        if spec.lever == "conversion":
            value, notes = 0.0, []
            last = windows[-1][0]
            for part in scopes:
                rates = {start: (goal_web.value / visits.value) for (s, start), (visits, goal_web) in web.items()
                         if s == part and visits.value and goal_web.value is not None}
                if last not in rates:
                    raise GuardViolation(13, f"рычаг {spec.id}: у кабинета {part} нет визитов за {last:%m.%Y}")
                best = max(rates, key=rates.get)
                visits = web[(part, last)][0].value
                uplift = max(0.0, visits * (rates[best] - rates[last]))
                value += uplift
                notes.append(f"{part}: визиты {last:%m.%Y} {visits:g} × (лучшая конверсия {rates[best] * 100:.3f}% "
                             f"в {best:%m.%Y} − {rates[last] * 100:.3f}%) = {uplift:.1f}")
            missing = (f"прирост, а не поток: веб-поток {last:%m.%Y} с конверсией «визит → {first.metric}» лучшего "
                       f"месяца {history}; " + "; ".join(notes) + "; пересекается с потолками веток-потоков")
        else:
            series = [x for x in combined if x.segment == f"{SEGMENT}={spec.id}"]
            best = max(series, key=lambda x: x.value)
            current = series[-1]
            value = best.value
            missing = (f"лучший месяц ветки за {history}: {_month(best)} = {best.value:g}; последний {_month(current)} "
                       f"= {current.value:g}; сезон окна цели не учтён")
        ceilings[spec.id] = Number(**common, flow=spec.flow, segment=f"{SEGMENT}={spec.id}",
                                   source=f"{source_class}:{MODEL_SYSTEM}:потолок ветки {spec.id}",
                                   status=Status.ESTIMATE, value=value, missing=missing)
    return combined, goal, ceilings


def run(args, out=print, adapter=None, today=date.today, bridge_factory=None, google_factory=None) -> int:
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    section = raw.get("goal_tree") or {}
    system = section.get("system") or (raw.get("funnel") or {}).get("system")
    breakdown = section.get("breakdown")
    target = section.get("target")
    if not system or not breakdown or isinstance(target, bool) or not isinstance(target, (int, float)):
        raise GuardViolation(9, "в конфигурации нет раздела goal_tree с «system», «breakdown» и числом «target»")
    specs = parse_branches(section, cfg.flows)
    history = section.get("history") or {}
    windows = parse_months(",".join(_months_between(str(history.get("from", "")), str(history.get("to", "")))))
    month = parse_months(args.month)[0]
    scopes = [s.strip() for s in str(section.get("scope") or "").split("+") if s.strip()]
    if not scopes:
        raise GuardViolation(9, "goal_tree.scope: нужен кабинет цели или сумма кабинетов, например atm+brz")
    if adapter is None:
        adapter = registry.build(system, raw, cfg, args.secrets)
    as_of = today()   # одна дата съёма на прогон (П3): цель, потолки и их основание — одного съёма
    out(f"Дерево цели: система «{system}», разрез «{breakdown}», история {windows[0][0]:%m.%Y}–{windows[-1][0]:%m.%Y}, "
        f"кабинеты {'+'.join(scopes)}, месяц цели {month[0]:%m.%Y}, дата съёма {as_of:%d.%m.%Y}")
    facts, web = collect(adapter, specs, cfg.goal_metric, scopes, windows, breakdown, as_of)
    combined, goal, ceilings = build(facts, web, specs, cfg, scopes, windows, target,
                                     str(section.get("target_note") or ""), month, as_of, system)
    tree = GoalTree(goal=goal, branches=tuple(Branch(id=s.id, name=s.name, ceiling=ceilings[s.id]) for s in specs))
    gap = tree.gap()                       # проверки единиц, окна, съёма, потока и системы — до записи

    for spec in specs:
        if not spec.lever:
            series = [x for x in combined if x.segment == f"{SEGMENT}={spec.id}"]
            out(f"{spec.id} «{spec.name}» по месяцам: " + ", ".join(f"{_month(x)} {x.value:g}" for x in series))
    out(f"цель: {render(goal)}")
    for spec in specs:
        out(f"потолок {spec.id} «{spec.name}»: {render(ceilings[spec.id])}")
    out(f"разрыв дерева цели: {render(gap)}")

    web_numbers = [x for pair in web.values() for x in pair]
    # При одном кабинете суммы — те же числа веток, писать их второй раз незачем.
    written = facts + (combined if len(scopes) > 1 else []) + web_numbers + [goal] + list(ceilings.values())
    if getattr(args, "dry_run", False):
        out(f"сухой прогон: чисел к записи {len(written)}, ничего не записано")
        return 0
    with open_store(args, cfg.storage_link_domains, today=today, bridge_factory=bridge_factory,
                    google_factory=google_factory) as (store, label):
        report = store.create_numbers(written)
    out(f"хранилище: {label}")
    out(f"«{report.artifact}»: записано {report.rows_written}, прочитано {report.rows_read_back}")
    ready = {"goal_id": number_id(goal),
             "branches": [{"id": s.id, "name": s.name, "ceiling_id": number_id(ceilings[s.id])} for s in specs]}
    out("для tree --json: " + json.dumps(ready, ensure_ascii=False))
    out(f"ИТОГ: веток {len(specs)}, чисел в прогоне {len(written)}; записано {report.rows_written}, "
        f"прочитано {report.rows_read_back}")
    return 0


def _months_between(first: str, last: str) -> list[str]:
    try:
        year, month = int(first[:4]), int(first[5:7])
        end = (int(last[:4]), int(last[5:7]))
    except ValueError:
        raise GuardViolation(9, f"goal_tree.history: нужны «from» и «to» вида ГГГГ-ММ, получено {first!r}, {last!r}")
    months = []
    while (year, month) <= end:
        months.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if not months:
        raise GuardViolation(9, f"goal_tree.history: окно {first}–{last} пустое")
    return months


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="growth_engine.goal_tree", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--month", required=True, help="месяц цели ГГГГ-ММ: окно, в котором стоят цель и потолки")
    add_store_arguments(parser, dry_run=True)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (GuardViolation, SourceError) as exc:
        print(f"❌ дерево цели не посчитано: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
