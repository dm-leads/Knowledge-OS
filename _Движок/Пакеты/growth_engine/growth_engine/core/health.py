"""Проверки гейта инстанса (этап 6, страж 13): чистая логика без ввода-вывода.

Гейт — исполняемая копия стандарта стражей. Каждая строка несёт раздел, номер стража, итог и что проверено. Итоги:
✅ пройдено; 🟥 структура — вывод по этим данным будет ложным; 🟧 покрытие — данных не хватает; ⬜ не проверено —
причину называет строка покрытия выше; ⏭ пропущено по правилу (платная проба без ключа). Данные передаёт команда гейта:
числа снимка модели, прочитанные записи реестра, листы хранилища, записи карты источников. Код итога 0 — проверки есть
и нет ни одной строки 🟥 или 🟧.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from .arithmetic import add
from .artifacts import check_secret_names, validate_route
from .economy import Tolerance, money_model, money_share, noise, per_unit
from .errors import GuardViolation
from .number import Number
from .registry import HStatus, check_state

MODEL_ROLES = ("users", "sales", "revenue", "cogs", "profit")
MONEY_ROLES = ("sales", "revenue", "cogs", "profit")
CABINET_DERIVED = ("c1", "avg_check", "margin", "amppu", "ampu")
CONNECTED = "подключён"
SECTION_SOURCES = "карта источников"
SECTION_MODEL = "модель"
SECTION_TAKES = "пересмотры"
SECTION_REGISTRY = "реестр"
SECTION_ARTIFACTS = "артефакты"
SECTION_KNOWN = "известные ответы"


class Outcome(str, Enum):
    PASSED = "✅"
    STRUCTURE = "🟥"
    COVERAGE = "🟧"
    NOT_CHECKED = "⬜"
    SKIPPED = "⏭"


FAILING = (Outcome.STRUCTURE, Outcome.COVERAGE)


@dataclass(frozen=True)
class Check:
    section: str
    guard: int
    outcome: Outcome
    text: str

    def render(self) -> str:
        return f"{self.outcome.value} [страж {self.guard}] {self.section}: {self.text}"


def violation(section: str, exc: GuardViolation, context: str = "") -> Check:
    """Нарушение стража — строкой гейта: тяжесть из исключения, текст без префикса «[страж N]»."""
    outcome = Outcome.COVERAGE if exc.severity == GuardViolation.COVERAGE else Outcome.STRUCTURE
    text, prefix = str(exc), f"[страж {exc.guard}] "
    body = text[len(prefix):] if text.startswith(prefix) else text
    return Check(section, exc.guard, outcome, f"{context}: {body}" if context else body)


def verdict(checks) -> int:
    """Код итога гейта: 0 — проверки есть и ни одна не дала строку структуры или покрытия."""
    checks = list(checks)
    return 0 if checks and not any(check.outcome in FAILING for check in checks) else 1


def summary(checks) -> str:
    checks = list(checks)
    count = {outcome: sum(check.outcome is outcome for check in checks) for outcome in Outcome}
    return (f"ИТОГ: {'гейт зелёный' if verdict(checks) == 0 else 'гейт НЕ пройден'} — пройдено {count[Outcome.PASSED]}, "
            f"структура {count[Outcome.STRUCTURE]}, покрытие {count[Outcome.COVERAGE]}, не проверено "
            f"{count[Outcome.NOT_CHECKED]}, пропущено {count[Outcome.SKIPPED]}")


def _day(value: date) -> str:
    return f"{value:%d.%m.%Y}"


def _window(x: Number) -> str:
    return f"{_day(x.period_start)}–{_day(x.period_end - timedelta(days=1))}"


def _amount(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:.4g}".replace(".", ",")


def _value(x: Number) -> str:
    return "нет данных" if x.value is None else _amount(x.value)


def _percent(share: float) -> str:
    return f"{share * 100:.1f}%".replace(".", ",")


def _key(x: Number) -> tuple:
    return (x.metric, x.scope, x.flow, x.segment, x.period_start, x.period_end)


def _reproduced(stored: Number, computed: Number) -> bool:
    if stored.status != computed.status or stored.unit != computed.unit:
        return False
    if stored.value is None or computed.value is None:
        return stored.value is None and computed.value is None
    return math.isclose(stored.value, computed.value, rel_tol=1e-9, abs_tol=1e-6)


# --- модель ---

def _compare(label: str, expected, stored: dict, passed: str) -> Check:
    compared, differ, absent = 0, [], []
    for number in expected:
        found = stored.get(_key(number))
        if found is None:
            absent.append(number.metric)
            continue
        compared += 1
        if not _reproduced(found, number):
            differ.append(f"{number.metric} — в снимке {_value(found)} ({found.status.value}), пересчёт "
                          f"{_value(number)} ({number.status.value})")
    if differ:
        return Check(SECTION_MODEL, 2, Outcome.STRUCTURE,
                     f"{label}: числа снимка не воспроизводятся кодом модели — " + "; ".join(differ))
    if absent:
        return Check(SECTION_MODEL, 13, Outcome.COVERAGE,
                     f"{label}: {passed}, но в снимке нет чисел, которые пишет прогон модели: {', '.join(absent)} — "
                     "снимок неполный, сверка с пересчётом не выполнена целиком")
    return Check(SECTION_MODEL, 2, Outcome.PASSED, f"{label}: {passed}, чисел сверено с пересчётом {compared}")


def check_model(numbers, roles: dict, cfg, today: date, max_age_days: int, money_system: str,
                pnl_tolerance: float = 1.0) -> list[Check]:
    """Модель последнего снимка. В снимок входят только числа денежной системы `money_system`: числа модели цикла (цели
    и потолки дерева, эффекты гипотез — система «модель») и других систем не подменяют базовые числа и не сдвигают дату
    снимка; дата снимка — последняя дата съёма чисел ролей денежной модели. Числа, которые пишет прогон модели, но
    которых в снимке нет, — покрытие: снимок неполный. Проверяется возраст
    снимка; по каждому кабинету и окну — тождество PnL и пересчёт C1, чека, маржи, AMPPU, AMPU и порога шума C1 кодом
    модели из сохранённых базовых чисел (страж 2; части разной природы — стражи 3–4); AMPU и маржа компании — из сумм
    кабинетов. Два разных числа одной роли в одном окне — структура: снимок неоднозначен. `roles` — роли денежной модели
    → метрики конфигурации."""
    role_metrics = {roles[role] for role in MODEL_ROLES if role in roles}
    measured = [number for number in numbers if number.as_of is not None and number.source_system == money_system]
    dates = [number.as_of for number in measured if number.metric in role_metrics]
    if not dates:
        return [Check(SECTION_MODEL, 13, Outcome.COVERAGE,
                      f"в хранилище нет чисел денежной системы «{money_system}» с датой съёма — модель не проверить")]
    as_of = max(dates)
    take = [number for number in measured if number.as_of == as_of]
    age = (today - as_of).days
    checks = [Check(SECTION_MODEL, 13, Outcome.COVERAGE if age > max_age_days else Outcome.PASSED,
                    f"снимок модели от {_day(as_of)}: {age} дн. при пределе {max_age_days}"
                    + (" — пересчитайте модель" if age > max_age_days else ""))]
    absent_roles = [role for role in MODEL_ROLES if role not in roles]
    if absent_roles:
        return checks + [Check(SECTION_MODEL, 9, Outcome.STRUCTURE,
                               f"в ролях денежной модели нет: {', '.join(absent_roles)}")]
    role_of = {roles[role]: role for role in MODEL_ROLES}
    stored = {_key(number): number for number in take}
    groups, clashes = {}, set()
    for number in take:
        role = role_of.get(number.metric)
        if role is None:
            continue
        key = (number.period_start, number.period_end, number.flow, number.segment, number.scope)
        group = groups.setdefault(key, {})
        if role in group and group[role] != number:
            clashes.add(key)
        group[role] = number
    models = {}
    for key in sorted(groups):
        group = groups[key]
        if not any(role in group for role in MONEY_ROLES):
            continue
        sample = next(iter(group.values()))
        label = f"кабинет {sample.scope}, окно {_window(sample)}"
        if key in clashes:
            checks.append(Check(SECTION_MODEL, 13, Outcome.STRUCTURE,
                                f"{label}: в снимке разные числа одной роли модели — снимок неоднозначен, пересчёт "
                                "невозможен"))
            continue
        missing = [f"{role} ({roles[role]})" for role in MODEL_ROLES if role not in group]
        if missing:
            checks.append(Check(SECTION_MODEL, 13, Outcome.COVERAGE,
                                f"{label}: в снимке нет базовых чисел модели — {', '.join(missing)}"))
            continue
        try:
            model = money_model(**group, cfg=cfg, pnl_tolerance=pnl_tolerance)
            expected = [getattr(model, name) for name in CABINET_DERIVED] + [noise(model.c1)]
        except GuardViolation as exc:
            checks.append(violation(SECTION_MODEL, exc, label))
            continue
        models.setdefault(key[:4], []).append(model)
        checks.append(_compare(label, expected, stored, "тождество PnL сходится"))
    for window in sorted(models):
        cabinets = models[window]
        if len(cabinets) < 2:
            continue
        label = f"компания, окно {_window(cabinets[0].users)}"
        try:
            profit, users, revenue = (add([getattr(model, role) for model in cabinets], cfg)
                                      for role in ("profit", "users", "revenue"))
            expected = [per_unit(profit, users, "ampu", cfg), money_share(profit, revenue, "margin", cfg)]
        except GuardViolation as exc:
            checks.append(violation(SECTION_MODEL, exc, label))
            continue
        checks.append(_compare(f"компания {users.scope}, окно {_window(users)}", expected, stored,
                               "суммы кабинетов сходятся"))
    return checks


# --- пересмотры между съёмами ---

def compare_takes(earlier: Number, later: Number, tolerance: Tolerance) -> Check:
    """Одна величина одной системы в двух съёмах — пересмотр задним числом и запаздывание флага (Т6). Сдвиг вне допуска —
    покрытие; допуск — как у моста: нарушение, только когда превышены оба порога. Разные величины, системы или порядок
    съёмов — структура (страж 4)."""
    pairs = (("метрика", earlier.metric, later.metric), ("кабинет", earlier.scope, later.scope),
             ("окно", (earlier.period_start, earlier.period_end), (later.period_start, later.period_end)),
             ("поток", earlier.flow, later.flow), ("разрез", earlier.segment, later.segment),
             ("единица", earlier.unit, later.unit), ("система", earlier.source_system, later.source_system))
    for name, a, b in pairs:
        if a != b:
            return Check(SECTION_TAKES, 4, Outcome.STRUCTURE, f"сравниваются разные величины: не совпадает {name} "
                                                              f"({a} / {b})")
    if earlier.as_of is None or later.as_of is None or not earlier.as_of < later.as_of:
        return Check(SECTION_TAKES, 4, Outcome.STRUCTURE, "сравнение съёмов: второй съём должен быть позже первого")
    head = (f"{later.metric} · кабинет {later.scope} · окно {_window(later)}: съём {_day(earlier.as_of)} — "
            f"{_value(earlier)}, съём {_day(later.as_of)} — {_value(later)}")
    if earlier.value is None or later.value is None:
        return Check(SECTION_TAKES, 13, Outcome.COVERAGE, f"{head} — нет данных для сравнения")
    diff = later.value - earlier.value
    rel = abs(diff) / abs(earlier.value) if earlier.value else (0.0 if diff == 0 else math.inf)
    ok = not (abs(diff) > tolerance.abs_units and rel > tolerance.rel)
    return Check(SECTION_TAKES, 13, Outcome.PASSED if ok else Outcome.COVERAGE,
                 f"{head}: сдвиг {diff:+g} ({_percent(rel)}), {'в допуске' if ok else 'вне допуска'} (допуск "
                 f"{tolerance.abs_units:g} шт. или {_percent(tolerance.rel)})")


def closed_months(today: date, count: int) -> list[tuple[date, date]]:
    """Последние `count` закрытых месяцев до месяца `today` по порядку: окна [начало месяца, начало следующего)."""
    if count < 1:
        raise GuardViolation(9, f"число закрытых месяцев {count} — нужно не меньше 1")
    windows, end = [], date(today.year, today.month, 1)
    for _ in range(count):
        start = date(end.year - (end.month == 1), 12 if end.month == 1 else end.month - 1, 1)
        windows.append((start, end))
        end = start
    return windows[::-1]


def check_revisions(stored, fresh, tolerance: Tolerance) -> list[Check]:
    """Пересмотры задним числом: свежее число сравнивается с последним более ранним съёмом той же величины той же системы
    в хранилище. В хранилище только сегодняшний съём — сравнивать не с чем (не проверено); окна в хранилище нет —
    покрытие."""
    stored = [number for number in stored if number.as_of is not None]
    checks = []
    for number in fresh:
        same = [x for x in stored
                if _key(x) == _key(number) and x.unit == number.unit and x.source_system == number.source_system]
        earlier = [x for x in same if x.as_of < number.as_of]
        label = f"{number.metric} · кабинет {number.scope} · окно {_window(number)}"
        if earlier:
            checks.append(compare_takes(max(earlier, key=lambda x: x.as_of), number, tolerance))
        elif same:
            checks.append(Check(SECTION_TAKES, 13, Outcome.NOT_CHECKED,
                                f"{label}: в хранилище только съём {_day(number.as_of)} — сравнивать не с чем"))
        else:
            checks.append(Check(SECTION_TAKES, 13, Outcome.COVERAGE,
                                f"{label}: в хранилище нет съёма этого окна — пересмотр не проверить"))
    return checks


def check_known_answer(number: Number, expected: float, tolerance: Tolerance, checked: str) -> Check:
    """Живой известный ответ: адаптер воспроизводит число, сверенное раньше. Вне допуска — структура (страж 2): источник
    или адаптер перестали давать проверенное число; нет данных — покрытие. Допуск — как у моста: нарушение, только когда
    превышены оба порога."""
    head = (f"{number.metric} · кабинет {number.scope} · окно {_window(number)} · съём {_day(number.as_of)}: "
            f"{_value(number)} против известного {_amount(expected)} {number.unit}")
    if number.value is None:
        return Check(SECTION_KNOWN, 13, Outcome.COVERAGE,
                     f"{head} — нет данных" + (f": {number.missing}" if number.missing else ""))
    diff = number.value - expected
    rel = abs(diff) / abs(expected) if expected else (0.0 if diff == 0 else math.inf)
    ok = not (abs(diff) > tolerance.abs_units and rel > tolerance.rel)
    return Check(SECTION_KNOWN, 2, Outcome.PASSED if ok else Outcome.STRUCTURE,
                 f"{head}: разница {diff:+g} ({_percent(rel)}), {'в допуске' if ok else 'вне допуска'} (допуск "
                 f"{tolerance.abs_units:g} {number.unit} или {_percent(tolerance.rel)}); сверено: {checked}")


# --- реестр ---

def check_registry(hypotheses, knowledge, decisions, trees, routes, class_status: dict, today: date) -> list[Check]:
    """Реестр: каждая гипотеза, кроме архива, проходит check_state() — одна сломанная карточка не останавливает проверку
    остальных. Сверх него: отложенный замер с прошедшей датой и окно теста без замера (покрытие), вывод без своей строки
    знаний (страж 14), решение без вычитания (страж 15), ветка, которой нет в дереве цели (покрытие), маршрут против
    статусов классов карты источников."""
    hypotheses, knowledge, decisions, trees, routes = map(list, (hypotheses, knowledge, decisions, trees, routes))
    checks = []
    rows = {entry.id: entry for entry in knowledge}
    branches = {branch.id for tree in trees for branch in tree.branches}
    for h in hypotheses:
        if h.status is HStatus.ARCHIVE:
            continue
        try:
            check_state(h)
        except GuardViolation as exc:
            checks.append(violation(SECTION_REGISTRY, exc))
            continue
        if h.status is HStatus.DEFERRED and today > h.deferred_until:
            checks.append(Check(SECTION_REGISTRY, 13, Outcome.COVERAGE,
                                f"гипотеза {h.id}: отложенный замер назначен на {_day(h.deferred_until)} — дата прошла, "
                                "замера нет"))
        if h.status is HStatus.IN_TEST:
            ends = h.start_date + timedelta(days=h.window_days)
            if today > ends:
                checks.append(Check(SECTION_REGISTRY, 13, Outcome.COVERAGE,
                                    f"гипотеза {h.id}: окно теста закончилось {_day(ends - timedelta(days=1))}, замер "
                                    f"можно снять с {_day(ends)} — замера нет"))
        if h.status is HStatus.CONCLUDED:
            row = rows.get(h.knowledge_row)
            if row is None or row.hypothesis_id != h.id:
                checks.append(Check(SECTION_REGISTRY, 14, Outcome.STRUCTURE,
                                    f"гипотеза {h.id}: вывод ссылается на строку знаний «{h.knowledge_row}», а на карте "
                                    "знаний её нет или она о другой гипотезе"))
        if h.tree_branch and h.tree_branch not in branches:
            checks.append(Check(SECTION_REGISTRY, 13, Outcome.COVERAGE,
                                f"гипотеза {h.id}: ветки «{h.tree_branch}» нет в дереве цели"))
    for entry in decisions:
        if not entry.subtraction.strip():
            checks.append(Check(SECTION_REGISTRY, 15, Outcome.STRUCTURE,
                                f"запись журнала решений {entry.id or entry.decided}: не записано, что убрали"))
    for route in routes:
        try:
            validate_route(route, class_status)
        except GuardViolation as exc:
            checks.append(violation(SECTION_REGISTRY, exc, f"маршрут {route.cycle_id}"))
    if not checks:
        checks.append(Check(SECTION_REGISTRY, 13, Outcome.PASSED,
                            f"гипотез {len(hypotheses)}, строк знаний {len(knowledge)}, решений {len(decisions)}, "
                            f"деревьев цели {len(trees)}, маршрутов {len(routes)} — нарушений нет"))
    return checks


# --- семь листов и карта источников ---

def check_artifacts(sheets: dict) -> list[Check]:
    """Листы хранилища: значение — число строк листа или None, если листа нет (покрытие). Пустой лист — «артефакт
    заведён, не заполнен», не нарушение."""
    missing = [name for name, rows in sheets.items() if rows is None]
    if missing:
        return [Check(SECTION_ARTIFACTS, 13, Outcome.COVERAGE, f"листа «{name}» нет — артефакт не заведён")
                for name in missing]
    return [Check(SECTION_ARTIFACTS, 13, Outcome.PASSED,
                  f"листов {len(sheets)}: " + ", ".join(f"«{name}» — {rows}" for name, rows in sheets.items()))]


def check_source_map(entries, configured: dict, env_names) -> list[Check]:
    """Карта источников против конфигурации и файла секретов: имена переменных есть среди имён файла секретов (страж
    12); у каждого источника конфигурации есть подключённый источник того же класса в карте, у каждого подключённого
    класса карты — источник в конфигурации (покрытие). `configured` — имя раздела источника → класс A–F."""
    entries = list(entries)
    checks = []
    try:
        check_secret_names(entries, env_names)
    except GuardViolation as exc:
        checks.append(violation(SECTION_SOURCES, exc))
    connected = {entry.source_class for entry in entries if entry.status == CONNECTED}
    for name, source_class in sorted(configured.items()):
        if source_class not in connected:
            checks.append(Check(SECTION_SOURCES, 13, Outcome.COVERAGE,
                                f"источник конфигурации «{name}» класса {source_class or '—'}: в карте нет подключённого "
                                "источника этого класса"))
    for source_class in sorted(connected - set(configured.values())):
        checks.append(Check(SECTION_SOURCES, 13, Outcome.COVERAGE,
                            f"класс {source_class} подключён в карте, а в конфигурации нет его источника — движок его "
                            "не читает"))
    if not checks:
        checks.append(Check(SECTION_SOURCES, 12, Outcome.PASSED,
                            f"записей {len(entries)}, подключены классы {', '.join(sorted(connected))}; имена переменных "
                            "секретов есть в файле секретов; классы совпадают с конфигурацией"))
    return checks


def check_sources_sheet(file_entries, sheet_entries) -> Check:
    """Лист карты источников — снимок файла карты: расхождение — покрытие, карта выгружается заново."""
    sheet = {entry.name: entry for entry in sheet_entries}
    behind = [entry.name for entry in file_entries if sheet.get(entry.name) != entry]
    if behind:
        return Check(SECTION_SOURCES, 13, Outcome.COVERAGE,
                     f"лист карты источников отстаёт от файла карты: {', '.join(behind)} — выгрузите карту заново")
    return Check(SECTION_SOURCES, 13, Outcome.PASSED, f"лист карты источников совпадает с файлом карты: записей {len(sheet)}")
