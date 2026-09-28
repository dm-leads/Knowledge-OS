"""Команда реестра Движка роста: одна команда, подкомандами по шагам цикла (этап 7б).

Решение Р7б (15.09.2026, владелец): восемь отдельных модулей отклонены — «среди тысячи разных команд мы очень сильно
запутаемся». Поэтому одна команда с подкомандами по образцу `git`: общие ключи описаны один раз, имена подкоманд — слова
шагов алгоритма, имена артефактов — те же слова, что в коде хранилища (`sources`, `numbers`, `trees`, `routes`,
`hypotheses`, `knowledge`, `decisions`), без синонимов.

Правила, общие для всех подкоманд:
- число печатается только строкой `render()` — с периодом, знаменателем, источником, датой съёма и статусом;
- запись идёт только по явному ключу хранилища (`--out` или книга) или по книге из конфигурации, сухой прогон ничего
  не пишет;
- успех пишущей подкоманды — число записанных и прочитанных обратно строк, а не слово «готово»;
- нарушение стража печатается строкой с его номером и даёт код возврата 1, а не трассировку.

Запуск из Скрипты/:
  py -3 -m growth_engine.registry <подкоманда> --config <конфигурация> [ключи подкоманды]
"""
from __future__ import annotations

import argparse
import json as json_module
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import yaml

from .adapters import build
from .core.arithmetic import render
from .core.artifacts import (KNOWLEDGE_VERDICTS, Branch, DecisionEntry, GoalTree, KnowledgeEntry, close_cycle)
from .core.bottleneck import Bottleneck, Candidate, choose, stage_gaps
from .core.config import parse_config
from .core.cycle import (CREDIBILITY_FACTORS, EFFORT_FACTORS, Assumption, build_evidence, order_assumptions,
                         rat_priority, score)
from .core.economy import shift_share
from .core.errors import GuardViolation
from .core.ladders import UnitOfAction
from .core.number import Number, Status
from .core.registry import CHANGE_KINDS, Decision, HStatus, create, iterate, transition
from .core.router import check_hypothesis_in_route
from .core.storage import HYPOTHESIS_KINDS, number_id
from .storage.selection import STORE_KEYS_TEXT, add_store_arguments, given_store_keys, open_store, store_target


@dataclass(frozen=True)
class Spec:
    """Подкоманда: шаг цикла, пишет ли она в хранилище, и краткая справка для `--help`."""
    step: int
    writes: bool
    help: str


# Десять подкоманд: шаг 7 распадается на скоринг и гейт шума (разные стражи и разное время), evidence и shift-share
# служат нескольким шагам. Заготовки 7б.2–7б.8 наполняются поведением задачами этапа.
SUBCOMMANDS: dict[str, Spec] = {
    "bottleneck": Spec(3, False, "узкое место: ступень, перед которой копится запас, и спуск до единицы действия"),
    "hypothesis": Spec(6, True, "гипотеза: завести, показать по фильтру, перевести статус"),
    # Приоритет хранить негде: у гипотезы нет полей веры, сложности и балла, и хранилище их не пишет. Подкоманда
    # считает и печатает — помечать её пишущей значило бы обещать запись, которой не происходит.
    "score": Spec(7, False, "скоринг: вера по шести подфакторам, сложность по пяти, приоритет из эффекта в ₽"),
    "gate": Spec(7, True, "гейт шума: объявить ожидаемое изменение и порог до запуска"),
    "rat": Spec(8, False, "RAT: упорядочить допущения по P × цена ошибки ÷ цена проверки"),
    "evidence": Spec(6, False, "доказательства: собрать по номерам фактов, выдуманные вычеркнуть"),
    "measure": Spec(9, True, "замер: добавить замер теста, попадание в порог считает код"),
    "tree": Spec(3, True, "дерево цели: разрыв между целью и потолками ветвей, связь гипотезы с ветвью"),
    "close": Spec(10, True, "закрытие цикла: решение со строкой знаний и письменным вычитанием"),
    "shift-share": Spec(2, False, "разложение изменения доли на вклад структуры и вклад конверсии"),
}

# Подкоманды, которые считают по поданным полям и не открывают хранилище вовсе.
STORELESS = ("rat", "evidence")


def run(args, out=print, today=date.today, bridge_factory=None, adapter_factory=build) -> int:
    try:
        return _run(args, out, today, bridge_factory, adapter_factory)
    except GuardViolation as exc:
        out(f"❌ {args.cmd}: {exc}")
        return 1


def _config(path: str) -> tuple[dict, object]:
    file = Path(path)
    if not file.is_file():
        raise GuardViolation(9, f"конфигурация инстанса не найдена: «{path}»")
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    return raw, parse_config(raw)


def _run(args, out, today, bridge_factory, adapter_factory) -> int:
    spec = SUBCOMMANDS.get(args.cmd)
    if spec is None:
        raise GuardViolation(9, f"подкоманды «{args.cmd}» нет среди десяти: {', '.join(SUBCOMMANDS)}")
    raw, cfg = _config(args.config)
    if len(given_store_keys(args)) > 1:
        raise GuardViolation(13, f"хранилище: нужно ровно одно — {STORE_KEYS_TEXT}")
    if spec.writes and getattr(args, "dry_run", False):
        out("сухой прогон: ничего не записано")
        out(f"ИТОГ: {args.cmd} — сухой прогон, записано 0, прочитано 0")
        return 0
    # Подкоманды, работающие без хранилища: считают и печатают по поданным полям. Требовать от них ключ хранилища
    # значило бы просить доступ, который не нужен.
    if args.cmd in STORELESS:
        fields = json_module.loads(args.json) if getattr(args, "json", None) else {}
        if args.cmd == "rat":
            return _rat(fields, out)
        return _evidence(args, raw, cfg, fields, out, adapter_factory)
    target = store_target(args, raw)          # без ключа берётся книга из конфигурации; нет её — страж 9
    return _dispatch(args, spec, cfg, raw, target, out, today, bridge_factory)


def _rat(fields: dict, out) -> int:
    """RAT: формула упорядочивает допущения, а не оценивает их — поэтому печатается порядок и приоритет каждого."""
    given = fields.get("assumptions")
    if not isinstance(given, list) or not given:
        raise GuardViolation(9, "RAT: нужен непустой список допущений «assumptions» в --json")
    assumptions = [Assumption(statement=str(item.get("statement", "")), p_wrong=item.get("p_wrong"),
                              cost_of_error=item.get("cost_of_error"), cost_of_check=item.get("cost_of_check"),
                              removable=bool(item.get("removable", False))) for item in given]
    for number, assumption in enumerate(order_assumptions(assumptions), start=1):
        priority = rat_priority(assumption.p_wrong, assumption.cost_of_error, assumption.cost_of_check)
        mark = " — можно убрать, поэтому первым" if assumption.removable else ""
        out(f"{number}. {assumption.statement} — приоритет {priority:g}{mark}")
    out(f"ИТОГ: допущений {len(assumptions)}; проверять в этом порядке")
    return 0


def _evidence(args, raw: dict, cfg, fields: dict, out, adapter_factory) -> int:
    """Доказательства: номера фактов → фрагменты под утверждение «код факта = значение за период» (страж 10).

    Единственная подкоманда, которой нужен живой источник, поэтому секреты требует она сама, а не все десять.
    Цитаты не печатаются: в выводе номера, коды и причины вычёркивания.
    """
    if not getattr(args, "secrets", None):
        raise GuardViolation(9, "доказательства: нужен --secrets — база диалогов читается по секретам инстанса")
    code = fields.get("code")
    if not code:
        raise GuardViolation(10, "доказательства: не задан код факта — утверждение «код факта = значение за период»")
    ids = fields.get("ids") or []
    period = None
    if fields.get("period"):
        period = tuple(date.fromisoformat(x) for x in fields["period"])
    name = getattr(args, "source", None) or (raw.get("evidence") or {}).get("system") or "speech_analytics"
    adapter = adapter_factory(name, raw, cfg, args.secrets)
    evidence = build_evidence(ids, adapter.fragments, code, fields.get("value"), period)
    for fragment in evidence.fragments:
        out(f"✅ {fragment.id}: {fragment.code} = {fragment.value}; беседа {fragment.conversation} "
            f"({fragment.channel}) от {fragment.on:%d.%m.%Y}" + (f"; оговорка: {fragment.note}" if fragment.note else ""))
    for number, reason in evidence.struck:
        out(f"❌ {number}: {reason}")
    out(f"ИТОГ: фрагментов {len(evidence.fragments)}, вычеркнуто {len(evidence.struck)}")
    return 0


def _dispatch(args, spec: Spec, cfg, raw: dict, target, out, today, bridge_factory) -> int:
    """Исполнение подкоманды. Поведение шагов наполняется задачами 7б.5–7б.8; каркас держит общий контракт."""
    if args.cmd == "hypothesis":
        return _hypothesis(args, cfg, target, out, today, bridge_factory)
    if args.cmd == "tree":
        fields = json_module.loads(args.json) if getattr(args, "json", None) else {}
        with open_store(target, cfg.storage_link_domains, today, bridge_factory) as (store, label):
            if getattr(args, "id", None):
                return _link(store, args.id, fields, out, label)
            if not fields:
                return _show_tree(store, out)
            return _write_tree(store, fields, out, label)
    if args.cmd == "bottleneck":
        fields = json_module.loads(args.json) if getattr(args, "json", None) else {}
        with open_store(target, cfg.storage_link_domains, today, bridge_factory) as (store, label):
            return _bottleneck(store, cfg, raw, fields, out, label)
    if args.cmd == "shift-share":
        fields = json_module.loads(args.json) if getattr(args, "json", None) else {}
        with open_store(target, cfg.storage_link_domains, today, bridge_factory) as (store, label):
            return _shift_share(store, cfg, fields, out, label)
    if args.cmd == "close":
        fields = json_module.loads(args.json) if getattr(args, "json", None) else {}
        if not getattr(args, "id", None):
            raise GuardViolation(9, "закрытие цикла: нужен --id гипотезы с решением")
        with open_store(target, cfg.storage_link_domains, today, bridge_factory) as (store, label):
            return _close(store, args.id, getattr(args, "cycle", "") or "", fields, out, label)
    if args.cmd in ("score", "gate", "measure"):
        fields = json_module.loads(args.json) if getattr(args, "json", None) else {}
        if not getattr(args, "id", None):
            raise GuardViolation(9, f"{args.cmd}: нужен --id гипотезы, например H-001")
        with open_store(target, cfg.storage_link_domains, today, bridge_factory) as (store, label):
            found = store.read("hypotheses", id=args.id)
            if not found:
                raise GuardViolation(13, f"гипотезы {args.id} нет в реестре")
            if args.cmd == "score":
                return _score(found[0], fields, out)
            if args.cmd == "measure":
                return _measure(store, args.id, fields, out, label)
            return _gate(store, args.id, fields, out, label)
    raise GuardViolation(9, f"подкоманда «{args.cmd}» (шаг {spec.step}) ещё не собрана: задачи 7б.4–7б.8")


def _factors(fields: dict, key: str, names: tuple) -> dict:
    """Подфакторы из `--json`: состав объявлен ядром, лишний или пропущенный — ошибка (страж 9 внутри score())."""
    given = fields.get(key)
    if not isinstance(given, dict):
        raise GuardViolation(9, f"скоринг: нужен объект «{key}» с подфакторами: {', '.join(names)}")
    return {name: value for name, value in given.items()}


def _score(hypothesis, fields: dict, out) -> int:
    """Скоринг: считает и печатает. Хранилище не меняется — писать приоритет некуда, и обещать запись нельзя."""
    priority = score(_factors(fields, "вера", CREDIBILITY_FACTORS), _factors(fields, "сложность", EFFORT_FACTORS),
                     hypothesis.effect_rub)
    effort = f"{priority.effort:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    believed = f"{priority.credibility:.3f}".replace(".", ",")
    value = "не считается" if priority.value is None else f"{priority.value:,.0f}".replace(",", " ")
    out(f"{hypothesis.id}: сложность {effort}; вера {believed}; приоритет {value}")
    out(f"ИТОГ: {priority.note}")
    return 0


def _no_data(sample: Number, metric: str, unit: str, denominator: str, why: str) -> Number:
    """Непереданный кандидат — «нет данных» с причиной, а не ноль: ядро оставит его непроверенным (страж 1).

    Знаменатель задаётся и здесь: контракт числа запрещает долю без знаменателя даже без значения (страж 9), и ядро
    в таких случаях поступает так же — ставит знаменатель, а причину кладёт в оговорку.
    """
    return replace(sample, metric=metric, level="money", unit=unit, value=None, status=Status.NO_DATA,
                   denominator=denominator, missing=why)


def _money_pair(numbers: dict, fields: dict, name: str, title: str):
    """Пара чисел кандидата «сейчас / раньше» по номерам. Значением деньги не задаются (стражи 1 и 4)."""
    if fields.get(name) is not None and not fields.get(f"{name}_id"):
        raise GuardViolation(9, f"узкое место: «{name}» задаётся номером записанного числа — ключ «{name}_id», "
                                "а не значением")
    if not fields.get(f"{name}_id"):
        return None, None
    now = _number(numbers, fields[f"{name}_id"], title)
    before = _number(numbers, fields.get(f"{name}_before_id"), f"{title} прошлого окна")
    return now, before


def _bottleneck(store, cfg, raw: dict, fields: dict, out, label: str) -> int:
    """Узкое место шага 3: ступень, перед которой копится запас, и спуск до единицы действия.

    Ничего не пишет. Ворота шага: узкое место, названное метрикой, не принимается (страж 6) — команда говорит,
    чего не хватает, вместо того чтобы объявить шаг пройденным.
    """
    window, scope, flow = str(fields.get("window", "")), str(fields.get("scope", "")), str(fields.get("flow", ""))
    stages = fields.get("stages") or []
    if len(stages) < 2:
        raise GuardViolation(9, "узкое место: нужны минимум две ступени воронки в «stages»")
    numbers = store.read("numbers")
    # Воронка строится по числам системы-источника: числа модели (цель окна, потолки веток, эффект гипотезы) живут
    # рядом под той же метрикой и окном, но ступенями воронки не являются.
    system = str((raw.get("economy") or {}).get("money_system") or "")
    if not system:
        raise GuardViolation(9, "узкое место: в разделе economy конфигурации не указана система (money_system)")
    funnel = []
    for metric in stages:
        cfg.rule(str(metric))              # метрика вне конфигурации — страж 3 с её именем
        funnel += _window(numbers, str(metric), window, scope, flow, system)
    # В настоящем хранилище одна метрика лежит от нескольких съёмов. Числа разных съёмов не делятся (страж 4),
    # поэтому воронка берётся по последнему съёму — как это делает команда снимка. В папке прогона съём всегда один,
    # и первый живой цикл 17.09.2026 споткнулся именно здесь.
    takes = {x.as_of for x in funnel if x.as_of is not None}
    if len(takes) > 1:
        # Берётся съём, покрывающий больше ступеней, а при равном покрытии — более свежий: полная воронка важнее
        # свежести. Первая версия брала просто последний съём, и на живой книге воронка схлопнулась до одного
        # перехода, потому что в свежем съёме лежали только две ступени.
        best = max(takes, key=lambda take: (len({x.metric for x in funnel if x.as_of == take}), take))
        covered = len({x.metric for x in funnel if x.as_of == best})
        funnel = [x for x in funnel if x.as_of == best]
        out(f"съёмов в хранилище несколько — воронка по съёму {best:%d.%m.%Y}: ступеней {covered} из {len(stages)}")
    gaps = stage_gaps(funnel, cfg)
    out(f"хранилище: {label}; окно {window}; кабинет {scope}, поток {flow}")
    for gap in gaps:
        out(f"   {gap.upper.metric} → {gap.lower.metric}: {render(gap.conversion)}")
    by_id = {number_id(number): number for number in numbers}
    # Кандидаты перебираются по одному в порядке канона, поэтому неподанный чек не отменяет проверку просевшей маржи:
    # вместо обрыва ветки непереданный кандидат уходит в ядро как «нет данных» и остаётся непроверенным, а не «в
    # порядке». Первая версия требовала обе пары сразу и молча пропускала просевшую маржу.
    margin, margin_before = _money_pair(by_id, fields, "margin", "маржа")
    check, check_before = _money_pair(by_id, fields, "avg_check", "средний чек")
    sample = gaps[0].upper
    # Роли денежной модели лежат в сыром YAML, а не в разобранной конфигурации — так их берут route.py и snapshot.py.
    # Первая версия спрашивала `hasattr(cfg, "money_metrics")`: поля с таким именем у InstanceConfig нет, ветка была
    # мертва и молча подставляла «revenue» вместо «revenue_pnl» инстанса. Тесты этого не ловили — в фикстуре нет
    # раздела economy.
    roles = (raw.get("economy") or {}).get("money_metrics") or {}
    money_metric = roles.get("revenue") or "revenue"
    sales_metric = roles.get("sales") or "sales"
    margin = margin or _no_data(sample, "margin", "доля", money_metric, "маржа не подана")
    margin_before = margin_before or _no_data(sample, "margin", "доля", money_metric,
                                              "маржа прошлого окна не подана")
    check = check or _no_data(sample, "avg_check", f"₽ на {sales_metric}", sales_metric, "средний чек не подан")
    check_before = check_before or _no_data(sample, "avg_check", f"₽ на {sales_metric}", sales_metric,
                                            "чек прошлого окна не подан")
    choice = choose(gaps=gaps, cfg=cfg, margin=margin, margin_before=margin_before,
                    avg_check=check, avg_check_before=check_before)
    where = f"{choice.gap.upper.metric} → {choice.gap.lower.metric}" if choice.gap else choice.candidate.value
    out(f"узкое место: {choice.candidate.value} на переходе {where}")
    for note in choice.missing.split("; "):
        if note and "единица действия" not in note:
            out(f"   {note}")
    unit = fields.get("unit_of_action")
    if unit:
        choice = choice.with_unit(UnitOfAction(unit["source_class"], dict(unit["coordinates"])))
    if choice.unit_of_action is None:
        out("   единица действия не названа — узкое место не принято (страж 6, ворота шага 3)")
        out("ИТОГ: узкое место найдено, но шаг не закрыт: нужны координаты нижней ступени класса источника")
        return 0
    accepted = choice.accept()
    out("   " + accepted.line)
    out(f"ИТОГ: узкое место принято на единице действия; переходов {len(gaps)}")
    return 0


def _window(numbers, metric: str, month: str, scope: str, flow: str, system: str | None = None) -> list[Number]:
    """Числа одной метрики, окна, кабинета и потока — отбором из хранилища, а не по номерам: сегментов десятки.

    `system` сужает отбор до одной системы-источника. Без него одна метрика за одно окно приходит и от системы
    (факт), и от модели (цель окна, потолок ветки, эффект гипотезы): приёмочный прогон 7б показал, что тогда воронка
    строит ступень «метрика → та же метрика» и сама же отвергает её стражем 4 — шаг 3 становился непроходим при
    любом написании ключей.
    """
    chosen = [x for x in numbers
              if x.metric == metric and f"{x.period_start:%Y-%m}" == month and x.scope == scope and x.flow == flow
              and (system is None or x.source_system == system)]
    if not chosen:
        where = f"(кабинет {scope}, поток {flow}" + (f", система {system})" if system else ")")
        raise GuardViolation(13, f"в хранилище нет чисел «{metric}» за окно {month} {where}")
    return chosen


def _shift_share(store, cfg, fields: dict, out, label: str) -> int:
    """Разложение изменения доли на вклад структуры и вклад конверсии (страж 4).

    Ничего не пишет. Тождество «структура + конверсия = изменение» проверяет ядро — и это главное, ради чего
    разложение делается: без него оба вклада можно было бы назвать любыми числами.
    """
    needed = ("base_metric", "events_metric")
    missing = [name for name in needed if not fields.get(name)]
    if missing:
        raise GuardViolation(9, f"разложение: в --json нет {', '.join(missing)} — метрики базы и событий по сегментам")
    windows = fields.get("windows") or []
    if len(windows) != 2:
        raise GuardViolation(9, "разложение: нужны два окна в «windows», например [\"2026-05\", \"2026-08\"]")
    scope, flow = str(fields.get("scope", "")), str(fields.get("flow", ""))
    numbers = store.read("numbers")
    known = {x.metric for x in numbers}
    for name in needed:
        if fields[name] not in known:
            raise GuardViolation(13, f"разложение: метрики «{fields[name]}» нет в хранилище; есть "
                                     f"{', '.join(sorted(known))}")
    before, after = (str(window) for window in windows)
    sides = [_window(numbers, fields[metric], month, scope, flow)
             for month in (before, after) for metric in ("base_metric", "events_metric")]
    result = shift_share(sides[0], sides[1], sides[2], sides[3], cfg)
    out(f"хранилище: {label}; окна {before} → {after}; кабинет {scope}, поток {flow}")
    for number in (result.structure, result.within, result.total):
        out("   " + render(number))
    out(f"ИТОГ: тождество сходится — структура и конверсия дают изменение доли; сегментов "
        f"{len(sides[0])}")
    return 0


def _knowledge(fields: dict) -> KnowledgeEntry:
    """Строка карты знаний: вердикт из канона и источник обязательны — знание без источника не знание (страж 1)."""
    given = fields.get("knowledge")
    if not isinstance(given, dict):
        raise GuardViolation(14, "закрытие цикла: нужна строка карты знаний «knowledge» — что узнали о рынке")
    if given.get("verdict") not in KNOWLEDGE_VERDICTS:
        raise GuardViolation(14, f"знание: вердикт «{given.get('verdict')}» не из канона — нужно "
                                 f"{', '.join(KNOWLEDGE_VERDICTS)}")
    return KnowledgeEntry(id=str(given.get("id", "")), statement=str(given.get("statement", "")),
                          verdict=str(given["verdict"]), on=date.fromisoformat(given["on"]),
                          source=str(given.get("source", "")), hypothesis_id=str(given.get("hypothesis_id", "")))


def _decision_row(fields: dict, cycle_id: str) -> DecisionEntry:
    """Запись журнала решений: письменное вычитание — часть закрытия цикла, а не пожелание (страж 15)."""
    given = fields.get("decision_row")
    if not isinstance(given, dict):
        raise GuardViolation(15, "закрытие цикла: нужна запись журнала решений «decision_row» с полем «subtraction»")
    return DecisionEntry(cycle_id=str(given.get("cycle_id", cycle_id)), decided=str(given.get("decided", "")),
                         why=str(given.get("why", "")), subtraction=str(given.get("subtraction", "")),
                         alternatives=tuple(given.get("alternatives", ())), id=str(given.get("id", "")))


def _close(store, hid: str, cycle_id: str, fields: dict, out, label: str) -> int:
    """Закрытие цикла: решение, строка знаний и вычитание — вместе, иначе цикл не закрыт (стражи 14 и 15).

    Проверки ядра идут до записи: полузакрытого цикла в хранилище не остаётся.
    """
    found = store.read("hypotheses", id=hid)
    if not found:
        raise GuardViolation(13, f"гипотезы {hid} нет в реестре — закрывать нечего")
    hypothesis = found[0]
    knowledge, decision_row = _knowledge(fields), _decision_row(fields, cycle_id)
    decision = str(fields.get("decision", ""))
    # Перечень решений называется здесь: конструктор перечисления отвечает «is not a valid Decision» — по такой
    # строке агент не узнает, что писать.
    if decision not in [d.value for d in Decision]:
        raise GuardViolation(14, f"закрытие цикла: решение «{decision}» не из "
                                 f"{' / '.join(d.value for d in Decision)}")
    conclusion = str(fields.get("conclusion", ""))
    try:
        if decision == Decision.ITERATE.value:
            if not fields.get("next_formulation"):
                raise GuardViolation(14, "решение iterate: нужна формулировка следующей версии «next_formulation»")
            closed, successor = iterate(hypothesis, str(fields["next_formulation"]), conclusion, knowledge.id,
                                        str(fields.get("next_cycle", cycle_id)))
        else:
            closed, successor = transition(hypothesis, HStatus.CONCLUDED, decision=Decision(decision),
                                           conclusion=conclusion, knowledge_row=knowledge.id), None
    except ValueError as exc:                      # контракт переходов и перечень решений отвечают ValueError
        raise GuardViolation(14, f"гипотеза {hid}: {exc}")
    # Цикл проверяется целиком до записи: нет решения, чужой строки знаний или вычитания — стоп (стражи 14, 15).
    close_cycle(cycle_id, [closed], [knowledge], [decision_row])
    reports = store.create(knowledge) + store.create(decision_row)
    reports += store.update_status(hid, HStatus.CONCLUDED, decision=closed.decision, conclusion=closed.conclusion,
                                   knowledge_row=closed.knowledge_row)
    if successor is not None:
        reports += store.create(successor)
    out(f"хранилище: {label}")
    _report(reports, out)
    if successor is not None:
        out(f"следующая версия: {successor.id} — «{successor.status.value}» в цикле {successor.cycle_id}")
    out(f"ИТОГ: цикл {cycle_id} закрыт решением «{closed.decision.value}»; знание {knowledge.id}; "
        f"убрали: {decision_row.subtraction.strip()}")
    return 0


def _number(numbers: dict, reference, what: str) -> Number:
    """Число по номеру записанного: значением его задать нельзя — иначе стражи 1 и 4 обходятся ключом команды."""
    key = str(reference or "")
    if key not in numbers:
        raise GuardViolation(13, f"{what}: число {key or '—'} не найдено на листе снимков")
    return numbers[key]


def _write_tree(store, fields: dict, out, label: str) -> int:
    """Дерево цели: цель и потолки веток — номерами записанных чисел; разрыв считает ядро (страж 3)."""
    numbers = {number_id(number): number for number in store.read("numbers")}
    goal = _number(numbers, fields.get("goal_id"), "дерево цели")
    branches = tuple(Branch(id=str(item.get("id", "")), name=str(item.get("name", "")),
                            ceiling=_number(numbers, item.get("ceiling_id"), f"ветка {item.get('id', '—')}"),
                            # Единица действия, принятая шагом 3 на этой ветке: гипотеза шага 6 обязана стоять на ней.
                            unit_of_action=(UnitOfAction(item["unit_of_action"]["source_class"],
                                                         dict(item["unit_of_action"]["coordinates"]))
                                            if item.get("unit_of_action") else None))
                     for item in fields.get("branches") or ())
    tree = GoalTree(goal=goal, branches=branches)
    gap = tree.gap()                       # проверки единиц, окна, съёма, потока и системы — до записи
    reports = store.create(tree)
    out(f"хранилище: {label}")
    _report(reports, out)
    out(f"разрыв дерева цели: {render(gap)}")
    out(f"ИТОГ: дерево записано, веток {len(branches)}")
    return 0


def _show_tree(store, out) -> int:
    trees = store.read("trees")
    if not trees:
        out("ИТОГ: деревьев цели не найдено")
        return 0
    for tree in trees:
        out(f"цель: {render(tree.goal)}")
        for branch in tree.branches:
            linked = f"; гипотезы: {', '.join(branch.hypothesis_ids)}" if branch.hypothesis_ids else ""
            out(f"  ветка {branch.id} «{branch.name}»: потолок {render(branch.ceiling)}{linked}")
        out(f"  разрыв: {render(tree.gap())}")
    out(f"ИТОГ: деревьев {len(trees)}")
    return 0


def _link(store, hid: str, fields: dict, out, label: str) -> int:
    """Связь гипотезы с веткой: номер гипотезы — в список ветки, ветка — в поле гипотезы (операция хранилища)."""
    if not fields.get("branch_id"):
        raise GuardViolation(9, f"гипотеза {hid}: нужен «branch_id» — ветка дерева цели")
    if not store.read("hypotheses", id=hid):
        raise GuardViolation(13, f"гипотезы {hid} нет в реестре — связывать нечего")
    reports = store.link_to_tree(hid, str(fields.get("goal_id", "")), str(fields["branch_id"]))
    out(f"хранилище: {label}")
    _report(reports, out)
    out(f"ИТОГ: гипотеза {hid} связана с веткой {fields['branch_id']}")
    return 0


def _measure(store, hid: str, fields: dict, out, label: str) -> int:
    """Замер теста: число подаётся номером записанного, попадание в порог считает код (страж 7)."""
    if "measured_id" not in fields:
        raise GuardViolation(9, "замер: нужен «measured_id» — номер записанного числа; значением замер не задаётся")
    numbers = {number_id(number): number for number in store.read("numbers")}
    reference = str(fields["measured_id"])
    if reference not in numbers:
        raise GuardViolation(13, f"замер: число {reference} не найдено на листе снимков")
    reports = store.add_measurement(hid, numbers[reference])
    stored, = store.read("hypotheses", id=hid)
    out(f"хранилище: {label}")
    _report(reports, out)
    out(f"замер: {render(stored.measured)}")
    out(f"ИТОГ: статус гипотезы {hid} — «{stored.status.value}»; {stored.status_reason}")
    return 0


def _gate(store, hid: str, fields: dict, out, label: str) -> int:
    """Гейт шума: ожидание и порог объявляются до запуска; ниже порога — research решает код (страж 5)."""
    if "kind" not in fields:
        raise GuardViolation(9, f"гейт шума: не объявлен вид изменения — нужно {' или '.join(CHANGE_KINDS)}")
    if "change" not in fields:
        raise GuardViolation(9, "гейт шума: нужно ожидаемое изменение доли («change»), в долях: 0,01 = 1 п.п.")
    numbers = {number_id(number): number for number in store.read("numbers")}
    reference = str(fields.get("share_id", ""))
    if reference not in numbers:
        raise GuardViolation(13, f"гейт шума: доля {reference or '—'} не найдена на листе снимков")
    reports = store.declare_expectation(hid, numbers[reference], float(fields["change"]), fields["kind"])
    stored, = store.read("hypotheses", id=hid)
    out(f"хранилище: {label}")
    _report(reports, out)
    out(f"порог шума: {render(stored.noise_threshold)}" if stored.noise_threshold is not None
        else "порог шума: не определён")
    # Порог печатается строкой числа (в процентах), а карточка запуска принимает долю. Связь между ними нигде не была
    # видна, и агент переводил проценты в долю сам — приёмочный прогон 7б назвал это скрытым условием.
    if stored.noise_threshold is not None and stored.noise_threshold.value is not None:
        out(f"   для карточки запуска: threshold не ниже {stored.noise_threshold.value:.6f}".rstrip("0")
            + " (доля, не проценты)")
    reason = f" — {stored.status_reason}" if stored.status_reason else ""
    out(f"ИТОГ: статус гипотезы {hid} — «{stored.status.value}»{reason}")
    return 0


def _status(raw: str) -> HStatus:
    """Строка ключа → статус контракта. Неизвестный статус называется вместе со списком возможных (страж 9)."""
    try:
        return HStatus(raw)
    except ValueError:
        raise GuardViolation(9, f"статуса «{raw}» нет в контракте реестра: {', '.join(s.value for s in HStatus)}")


def _card(fields: dict, numbers: dict) -> dict:
    """Карточка гипотезы из `--json`: тексты как есть, числа — по номерам из хранилища, единица действия — координаты.

    Число не принимается значением: разбирать его здесь значило бы дать агенту написать любую величину с любым
    статусом мимо стражей 1 и 8. Числа кладёт в хранилище движок экономики, карточка на них ссылается.
    """
    # Поля-числа берутся из контракта хранилища, а не угадываются по суффиксу `_id`: первая версия принимала за номер
    # числа и `cycle_id` («Ц-1») — эвристика по имени ловила идентификатор цикла.
    references = {f"{name}_id": name for name, kind in HYPOTHESIS_KINDS.items() if kind == "number"}
    card = {name: value for name, value in fields.items()
            if name not in ("formulation", "unit_of_action") and name not in references}
    for name, field in references.items():
        if name not in fields:
            continue
        reference = str(fields[name])
        if reference not in numbers:
            raise GuardViolation(13, f"гипотеза: число {reference} ({field}) не найдено на листе снимков")
        card[field] = numbers[reference]
    if "unit_of_action" in fields:
        unit = fields["unit_of_action"]
        card["unit_of_action"] = UnitOfAction(unit["source_class"], dict(unit["coordinates"]))
    return card


def _to_candidate(hypothesis, card: dict):
    """Кандидат получается переходами, а не созданием: `create()` заводит только «идею» (контракт реестра)."""
    return transition(transition(hypothesis, HStatus.RESEARCH), HStatus.CANDIDATE, **card)


def _report(reports, out) -> None:
    """Запись идёт на несколько листов: сама запись и числа, на которые она ссылается. Отчёт — по каждому листу."""
    for report in reports:
        out(f"«{report.artifact}»: записано {report.rows_written}, прочитано {report.rows_read_back}")


def _hypothesis(args, cfg, target, out, today, bridge_factory) -> int:
    """Завести гипотезу, показать по фильтру или перевести статус — по набору поданных ключей."""
    fields = json_module.loads(args.json) if getattr(args, "json", None) else {}
    wanted = _status(args.status) if getattr(args, "status", None) else None
    with open_store(target, cfg.storage_link_domains, today, bridge_factory) as (store, label):
        if not fields and not getattr(args, "id", None):
            return _list(store, wanted, out)
        if not getattr(args, "id", None):
            raise GuardViolation(9, "гипотеза: нужен --id, например H-001")
        if wanted is not None and store.read("hypotheses", id=args.id):
            # Гипотеза уже в реестре — значит это перевод статуса, а не заведение: поля из `--json` идут карточкой
            # перехода (запуск теста требует десяти полей, отложенный замер — своей даты).
            return _move(store, args.id, wanted, fields, out, label)
        if not fields:
            return _move(store, args.id, wanted, {}, out, label)
        return _create(store, args.id, fields, wanted, out, label)


def _list(store, wanted: HStatus | None, out) -> int:
    """Показ по фильтру: пустая выборка называется словами — молчание читается как успех."""
    found = store.read("hypotheses", **({"status": wanted} if wanted else {}))
    for hypothesis in found:
        out(f"{hypothesis.id} [{hypothesis.status.value}] {hypothesis.formulation}")
    if not found:
        out("ИТОГ: гипотез по фильтру не найдено")
        return 0
    out(f"ИТОГ: гипотез {len(found)}")
    return 0


def _check_route(store, hypothesis) -> None:
    """Связь шагов цикла: гипотеза встаёт в согласованный маршрут, а не появляется сама по себе.

    Приёмочный прогон 7б показал дыру: гипотезу завели при непройденном шаге 3, и ничто этому не помешало. Проверка
    в ядре была (`check_hypothesis_in_route`), но команда её не звала. Маршрута в хранилище нет — проверять нечего:
    команда не требует того, чего владелец ещё не согласовал.
    """
    routes = [r for r in store.read("routes", verify=False) if r.cycle_id == hypothesis.cycle_id]
    for route in routes:
        check_hypothesis_in_route(route, hypothesis)
    _check_unit_of_action(store, hypothesis)


def _check_unit_of_action(store, hypothesis) -> None:
    """Гипотеза стоит на той единице действия, которую принял шаг 3 на её ветке дерева цели.

    Канон связывает шаги 3 и 6 через единицу действия: диагноз доводится до конкретной кнопки, и гипотеза пишется
    про неё же. До правки команда принимала любую единицу — диагноз и гипотеза могли жить порознь (ворота 7б).
    Ветка без принятой единицы не мешает: шаг 3 по ней ещё не проходил.
    """
    if not hypothesis.tree_branch:
        return
    for tree in store.read("trees", verify=False):
        for branch in tree.branches:
            if branch.id != hypothesis.tree_branch or branch.unit_of_action is None:
                continue
            if branch.unit_of_action != hypothesis.unit_of_action:
                accepted = ", ".join(f"{k}={v}" for k, v in branch.unit_of_action.coordinates.items())
                raise GuardViolation(6, f"гипотеза {hypothesis.id}: единица действия не та, что принята шагом 3 на "
                                        f"ветке {branch.id} — там {accepted}")


def _create(store, hid: str, fields: dict, wanted: HStatus | None, out, label: str) -> int:
    if not fields.get("formulation"):
        raise GuardViolation(9, "гипотеза: нужна формулировка «если — то — потому что» в --json")
    numbers = {number_id(number): number for number in store.read("numbers")}
    card = _card(fields, numbers)
    hypothesis = create(id=hid, formulation=fields["formulation"])
    if wanted is HStatus.CANDIDATE:
        hypothesis = _to_candidate(hypothesis, card)
        _check_route(store, hypothesis)
    elif wanted is not None and wanted is not HStatus.IDEA:
        raise GuardViolation(9, f"гипотеза заводится как «идея» или «candidate»; «{wanted.value}» — переводом статуса "
                                "после заведения")
    reports = store.create(hypothesis)
    stored = store.read("hypotheses", id=hid)
    out(f"хранилище: {label}")
    _report(reports, out)
    out(f"ИТОГ: {'гипотеза записана и прочитана' if stored else 'гипотеза в хранилище НЕ найдена'}")
    return 0 if stored else 1


DATE_FIELDS = ("start_date", "deferred_until")
TUPLE_FIELDS = ("rat",)


def _changes(fields: dict) -> dict:
    """Поля перехода из `--json`: даты строками ISO, список RAT — кортежем, как их хранит контракт."""
    changes = {name: value for name, value in fields.items() if name not in DATE_FIELDS + TUPLE_FIELDS}
    for name in DATE_FIELDS:
        if fields.get(name):
            changes[name] = date.fromisoformat(fields[name])
    for name in TUPLE_FIELDS:
        if name in fields:
            changes[name] = tuple(fields[name])
    return changes


def _move(store, hid: str, wanted: HStatus | None, fields: dict, out, label: str) -> int:
    """Перевод статуса — только через операцию хранилища: стражи переходов реестра действуют и здесь (Х9).

    Карточка перехода идёт полями: запуск теста требует десяти (`LAUNCH_FIELDS`), отложенный замер — своей даты.
    Без них командой нельзя было бы запустить тест, и замерять было бы нечего.
    """
    if wanted is None:
        raise GuardViolation(9, f"гипотеза {hid}: нужен --status — целевой статус перехода")
    if not store.read("hypotheses", id=hid):
        raise GuardViolation(13, f"гипотезы {hid} нет в реестре — переводить нечего")
    try:
        reports = store.update_status(hid, wanted, **_changes(fields))
    except ValueError as exc:                      # контракт переходов отвечает ValueError, а не стражем
        raise GuardViolation(7, f"гипотеза {hid}: {exc}")
    out(f"хранилище: {label}")
    _report(reports, out)
    stored, = store.read("hypotheses", id=hid)
    out(f"ИТОГ: статус гипотезы {hid} — «{stored.status.value}»")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Реестр Движка роста: подкоманды по шагам цикла")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, spec in SUBCOMMANDS.items():
        p = sub.add_parser(name, help=spec.help)
        p.add_argument("--config", required=True, help="конфигурация инстанса")
        p.add_argument("--cycle", default="", help="идентификатор цикла, например Ц-1")
        p.add_argument("--id", default=None, help="идентификатор записи, например H-001")
        p.add_argument("--status", default=None, help="фильтр или целевой статус")
        p.add_argument("--json", default=None, help="поля записи в JSON")
        p.add_argument("--secrets", default=None, help="файл секретов; нужен подкоманде доказательств")
        p.add_argument("--source", default=None, help="система-источник фактов; по умолчанию — из конфигурации")
        add_store_arguments(p, dry_run=spec.writes, required=False)
    # Кодировка чинится ДО разбора аргументов: `--help` печатается и завершает процесс внутри `parse_args()`,
    # поэтому справка со знаком вне кодировки консоли иначе падает трассировкой (приёмочный прогон 7б).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
