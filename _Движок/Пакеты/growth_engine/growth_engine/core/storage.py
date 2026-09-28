"""Контракт хранилища реестра (слой 5): строки семи артефактов, номер числа, кодирование ячеек, отчёт о записи.

Хранилище — таблица: лист на артефакт, строка на запись, колонки по именам полей (Х11). Числа внутри гипотез и дерева
цели хранятся ссылкой на лист «Модель — снимки» по устойчивому номеру (Х2). Ячейка — всегда текст канонической формы:
текст, который таблица приняла бы за формулу, логическое значение или ошибку, экранируется ведущим апострофом (Х12,
проба 15.09.2026). Шесть операций контракта и проверки состояния — задача 5.3; файлы и мосты к таблицам — `storage/`.
Успех записи отчитывается числом строк, прочитанных обратно, а не словом «готово».
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, fields, replace
from datetime import date
from typing import Protocol

from .artifacts import (Branch, CycleRoute, DecisionEntry, GoalTree, KnowledgeEntry, RouteStep, SourceMapEntry,
                        validate_route)
from .cycle import apply_noise_gate, measure
from .errors import GuardViolation
from .ladders import UnitOfAction
from .number import Number, Status
from .registry import Decision, HStatus, Hypothesis, check_state, transition

# Семь артефактов контракта реестра: ключ → имя листа.
SHEETS = {
    "sources": "Карта источников",
    "numbers": "Модель — снимки",
    "trees": "Дерево цели",
    "routes": "Маршрут цикла",
    "hypotheses": "Гипотезы",          # реестр и есть этот лист — решение владельца 24.09.2026
    "knowledge": "Карта знаний",
    "decisions": "Журнал решений",
}
# Поля идентичности числа: значение, статус и оговорка в номер не входят — их расхождение и есть конфликт.
NUMBER_IDENTITY = ("metric", "level", "scope", "flow", "segment", "period_start", "period_end", "as_of", "source",
                   "denominator", "unit")
ESCAPE = "'"
YES, NO = "да", "нет"
# Пробелы, которые таблица заменяет молча: неразрывный, цифровой, узкий неразрывный и неразрывный разделитель слов.
INVISIBLE_SPACES = re.compile("[   ⁠]")

NUMBER_FIELDS = (("metric", "text"), ("level", "text"), ("scope", "text"), ("flow", "text"), ("segment", "text"),
                 ("period_start", "date"), ("period_end", "date"), ("as_of", "date"), ("source", "text"),
                 ("status", "status"), ("value", "float"), ("denominator", "opt_text"), ("unit", "text"),
                 ("missing", "text"))
HYPOTHESIS_KINDS = {
    "status": "hstatus", "version": "int", "supersedes": "opt_text", "unit_of_action": "unit",
    "fact_basis": "number", "effect_goal_units": "number", "effect_rub": "number", "start_date": "date",
    "window_days": "int", "expected_n": "int", "threshold": "float", "expected_delta": "float",
    "noise_share": "number", "noise_threshold": "number", "rat": "json_tuple", "deferred_until": "date",
    "measured": "number", "in_threshold": "bool", "decision": "decision",
}
ROUTE_FIELDS = (("cycle_id", "text"), ("goal", "text"), ("written_on", "date"), ("expected_confidence", "text"),
                ("main_class", "text"), ("notes", "json_tuple"), ("closed_at", "int"), ("closed_reason", "text"),
                ("sales_goal", "bool"))
STEP_FIELDS = (("step", "int"), ("classes", "json"), ("substitute", "text"), ("extraction_cost", "text"),
               ("skipped_reason", "text"), ("no_data_reason", "text"))
KNOWLEDGE_KINDS = {"on": "date"}
DECISION_KINDS = {"alternatives": "json_tuple"}
SOURCE_KINDS = {"traps": "json_tuple", "secret_env_names": "json_tuple"}
_ENUMS = {"status": Status, "hstatus": HStatus, "decision": Decision}


@dataclass(frozen=True)
class WriteReport:
    artifact: str
    rows_written: int
    rows_read_back: int

    def __post_init__(self):
        if self.rows_written != self.rows_read_back:
            raise GuardViolation(13, f"«{self.artifact}»: записано {self.rows_written}, "
                                     f"прочитано обратно {self.rows_read_back}")


# --- ячейки ---

def encode_text(text: str) -> str:
    """Текст, который таблица превратила бы в формулу, логическое значение или ошибку, получает ведущий апостроф.

    Невидимые пробелы приводятся к обычному до записи: табличное хранилище молча заменяет неразрывный пробел
    (U+00A0) на обычный, и номер числа, посчитанный по прочитанному содержимому, перестаёт совпадать с записанным —
    строка читается как изменённая вне движка (страж 13). Названия разрезов у источников такие пробелы несут, и
    первый живой цикл на этом остановился. Движок нормализует сам, поэтому записанное и прочитанное совпадают.
    """
    text = INVISIBLE_SPACES.sub(" ", unicodedata.normalize("NFC", text))
    if text[:1] in ("=", ESCAPE, "#") or text.strip().lower() in ("true", "false"):
        return ESCAPE + text
    return text


def decode_text(cell) -> str:
    """Движок пишет только текст: иное значение в ячейке — правка вне движка или чтение не тем путём."""
    if not isinstance(cell, str):
        raise GuardViolation(13, f"ячейка {cell!r} — не текст: лист изменён вне движка или прочитан не тем путём")
    return cell[1:] if cell.startswith(ESCAPE) else cell


def _plain_json(value):
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain_json(item) for key, item in value.items()}
    return value


def _encode(value, kind: str) -> str:
    if value is None:
        return ""
    if kind in ("text", "opt_text"):
        text = str(value)
    elif kind == "int":
        text = str(int(value))
    elif kind == "float":
        text = repr(float(value))
    elif kind == "date":
        text = value.isoformat()
    elif kind == "bool":
        text = YES if value else NO
    elif kind in _ENUMS:
        text = value.value
    elif kind in ("json", "json_tuple"):
        text = json.dumps(_plain_json(value), ensure_ascii=False, separators=(",", ":"))
    elif kind == "unit":
        text = json.dumps({"source_class": value.source_class, "coordinates": _plain_json(value.coordinates)},
                          ensure_ascii=False, separators=(",", ":"))
    else:
        raise GuardViolation(13, f"вид ячейки «{kind}» не описан")
    return encode_text(text)


def _decode(cell, kind: str):
    text = decode_text(cell)
    if kind == "text":
        return text
    if text == "":
        return None
    try:
        if kind == "opt_text":
            return text
        if kind == "int":
            return int(text)
        if kind == "float":
            return float(text)
        if kind == "date":
            return date.fromisoformat(text)
        if kind == "bool":
            return {YES: True, NO: False}[text]
        if kind in _ENUMS:
            return _ENUMS[kind](text)
        if kind == "json":
            return json.loads(text)
        if kind == "json_tuple":
            return tuple(json.loads(text))
        if kind == "unit":
            data = json.loads(text)
            return UnitOfAction(data["source_class"], data["coordinates"])
    except (ValueError, KeyError, TypeError):
        raise GuardViolation(13, f"ячейка «{text[:40]}» не читается как «{kind}»") from None
    raise GuardViolation(13, f"вид ячейки «{kind}» не описан")


def _cell(row: dict, column: str):
    if column not in row:
        raise GuardViolation(13, f"в строке нет колонки «{column}» — шапка листа не совпадает со схемой движка")
    return row[column]


# --- числа ---

def number_id(number: Number) -> str:
    """Устойчивый номер числа: первые 16 знаков sha256 от канонического JSON полей идентичности (строки в NFC)."""
    parts = []
    for name in NUMBER_IDENTITY:
        value = getattr(number, name)
        if isinstance(value, date):
            value = value.isoformat()
        # Невидимые пробелы приводятся к обычному и здесь: иначе номер считается по исходному тексту, а в хранилище
        # уходит нормализованный — и число перестаёт сходиться с собственным номером после чтения.
        parts.append(INVISIBLE_SPACES.sub(" ", unicodedata.normalize("NFC", "" if value is None else str(value))))
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def number_row(number: Number) -> dict:
    row = {"id": number_id(number)}
    row.update({name: _encode(getattr(number, name), kind) for name, kind in NUMBER_FIELDS})
    return row


def number_from_row(row: dict) -> Number:
    number = Number(**{name: _decode(_cell(row, name), kind) for name, kind in NUMBER_FIELDS})
    stored = decode_text(_cell(row, "id"))
    if stored != number_id(number):
        raise GuardViolation(13, f"номер числа {stored} не совпадает с содержимым строки — строка изменена вне движка")
    return number


def numbers_by_id(rows) -> dict[str, Number]:
    result = {}
    for row in rows:
        number = number_from_row(row)
        key = number_id(number)
        if key in result and result[key] != number:
            raise GuardViolation(13, f"у числа {key} два разных содержимых — история одного съёма переписана")
        result[key] = number
    return result


def _resolve(numbers: dict, reference: str, owner: str, field: str) -> Number | None:
    if not reference:
        return None
    if reference not in numbers:
        raise GuardViolation(13, f"{owner}: число {reference} ({field}) не найдено на листе «{SHEETS['numbers']}»")
    return numbers[reference]


# --- гипотезы ---

def _hypothesis_kind(field) -> str:
    return HYPOTHESIS_KINDS.get(field.name, "text")


def hypothesis_row(hypothesis: Hypothesis) -> tuple[dict, list[Number]]:
    """Строка реестра гипотез и числа, на которые она ссылается (их пишут на лист снимков раньше строки)."""
    row, numbers = {}, []
    for field in fields(Hypothesis):
        value, kind = getattr(hypothesis, field.name), _hypothesis_kind(field)
        if kind == "number":
            row[f"{field.name}_id"] = "" if value is None else number_id(value)
            if value is not None:
                numbers.append(value)
        else:
            row[field.name] = _encode(value, kind)
    return row, numbers


def hypothesis_from_row(row: dict, numbers: dict) -> Hypothesis:
    owner = f"гипотеза {decode_text(_cell(row, 'id'))}"
    values = {}
    for field in fields(Hypothesis):
        kind = _hypothesis_kind(field)
        if kind == "number":
            values[field.name] = _resolve(numbers, decode_text(_cell(row, f"{field.name}_id")), owner, field.name)
        else:
            values[field.name] = _decode(_cell(row, field.name), kind)
    return Hypothesis(**values)


# --- маршрут цикла ---

def route_rows(route: CycleRoute) -> list[dict]:
    """Строка на шаг; поля маршрута повторяются в каждой строке и при чтении обязаны совпасть."""
    if not route.steps:
        raise GuardViolation(13, f"маршрут {route.cycle_id} без шагов не хранится")
    head = {name: _encode(getattr(route, name), kind) for name, kind in ROUTE_FIELDS}
    return [{"id": encode_text(f"{route.cycle_id}#{step.step}"), **head,
             **{name: _encode(getattr(step, name), kind) for name, kind in STEP_FIELDS}}
            for step in route.steps]


def routes_from_rows(rows) -> list[CycleRoute]:
    grouped = {}
    for row in rows:
        head = tuple(_decode(_cell(row, name), kind) for name, kind in ROUTE_FIELDS)
        step = RouteStep(**{name: _decode(_cell(row, name), kind) for name, kind in STEP_FIELDS})
        cycle = head[0]
        if cycle not in grouped:
            grouped[cycle] = (head, [step])
        elif grouped[cycle][0] != head:
            raise GuardViolation(13, f"маршрут {cycle}: строка шага {step.step} расходится с полями маршрута в других "
                                     "строках")
        else:
            grouped[cycle][1].append(step)
    names = [name for name, _ in ROUTE_FIELDS]
    return [CycleRoute(**dict(zip(names, head)), steps=tuple(steps)) for head, steps in grouped.values()]


# --- дерево цели ---

def tree_rows(tree: GoalTree) -> tuple[list[dict], list[Number]]:
    if not tree.branches:
        raise GuardViolation(13, "дерево цели без веток не хранится")
    goal = number_id(tree.goal)
    rows = [{"id": encode_text(f"{goal}#{branch.id}"), "goal_id": goal, "branch_id": _encode(branch.id, "text"),
             "name": _encode(branch.name, "text"), "ceiling_id": number_id(branch.ceiling),
             "hypothesis_ids": _encode(branch.hypothesis_ids, "json_tuple"),
             # Единица действия, принятая шагом 3: пустая ячейка — шаг по этой ветке ещё не проходил.
             "unit_of_action": "" if branch.unit_of_action is None else _encode(branch.unit_of_action, "unit"),
             "no_hypotheses_reason": _encode(branch.no_hypotheses_reason, "text")}
            for branch in tree.branches]
    return rows, [tree.goal] + [branch.ceiling for branch in tree.branches]


def trees_from_rows(rows, numbers: dict) -> list[GoalTree]:
    grouped = {}
    for row in rows:
        goal = decode_text(_cell(row, "goal_id"))
        branch_id = _decode(_cell(row, "branch_id"), "text")
        owner = f"ветка {branch_id} дерева цели"
        branch = Branch(id=branch_id, name=_decode(_cell(row, "name"), "text"),
                        ceiling=_resolve(numbers, decode_text(_cell(row, "ceiling_id")), owner, "ceiling"),
                        hypothesis_ids=_decode(_cell(row, "hypothesis_ids"), "json_tuple") or (),
                        unit_of_action=_decode(_cell(row, "unit_of_action"), "unit"),
                        # Колонка появилась в версии 1.1.0: строки дерева, записанные раньше, её не имеют.
                        no_hypotheses_reason=_decode(row.get("no_hypotheses_reason", ""), "text"))
        grouped.setdefault(goal, []).append(branch)
    return [GoalTree(goal=_resolve(numbers, goal, "дерево цели", "goal"), branches=tuple(branches))
            for goal, branches in grouped.items()]


# --- карта знаний, журнал решений, карта источников ---

def _record_row(record, kinds: dict, row_id: str) -> dict:
    row = {"id": encode_text(row_id)}
    row.update({field.name: _encode(getattr(record, field.name), kinds.get(field.name, "text"))
                for field in fields(record) if field.name != "id"})
    return row


def _record_from_row(cls, row: dict, kinds: dict):
    values = {field.name: _decode(_cell(row, field.name), kinds.get(field.name, "text"))
              for field in fields(cls) if field.name != "id"}
    if any(field.name == "id" for field in fields(cls)):
        values["id"] = decode_text(_cell(row, "id"))
    return cls(**values)


def knowledge_row(entry: KnowledgeEntry) -> dict:
    return _record_row(entry, KNOWLEDGE_KINDS, entry.id)


def knowledge_from_row(row: dict) -> KnowledgeEntry:
    return _record_from_row(KnowledgeEntry, row, KNOWLEDGE_KINDS)


def decision_row(entry: DecisionEntry) -> dict:
    if not entry.id.strip():
        raise GuardViolation(13, f"запись журнала решений цикла {entry.cycle_id} без номера не хранится")
    return _record_row(entry, DECISION_KINDS, entry.id)


def decision_from_row(row: dict) -> DecisionEntry:
    return _record_from_row(DecisionEntry, row, DECISION_KINDS)


def source_row(entry: SourceMapEntry) -> dict:
    return _record_row(entry, SOURCE_KINDS, entry.name)


def source_from_row(row: dict) -> SourceMapEntry:
    entry = _record_from_row(SourceMapEntry, row, SOURCE_KINDS)
    if decode_text(_cell(row, "id")) != entry.name:
        raise GuardViolation(13, f"источник «{entry.name}»: номер строки не совпадает с именем источника")
    return entry


def _hypothesis_columns() -> tuple[str, ...]:
    return tuple(f"{field.name}_id" if _hypothesis_kind(field) == "number" else field.name
                 for field in fields(Hypothesis))


COLUMNS = {
    "numbers": ("id",) + tuple(name for name, _ in NUMBER_FIELDS),
    "hypotheses": _hypothesis_columns(),
    "routes": ("id",) + tuple(name for name, _ in ROUTE_FIELDS) + tuple(name for name, _ in STEP_FIELDS),
    "trees": ("id", "goal_id", "branch_id", "name", "ceiling_id", "hypothesis_ids", "unit_of_action",
              "no_hypotheses_reason"),
    "knowledge": ("id",) + tuple(field.name for field in fields(KnowledgeEntry) if field.name != "id"),
    "decisions": ("id",) + tuple(field.name for field in fields(DecisionEntry) if field.name != "id"),
    "sources": ("id",) + tuple(field.name for field in fields(SourceMapEntry)),
}


# --- ПДн и секреты перед записью (страж 12) ---

# Телефон: российский номер в разных записях или международный с плюсом. Границы — не буква, не цифра и не десятичный
# разделитель: шестнадцатеричные номера чисел, uuid фактов и дробная часть значения (найдено прогоном модели через
# контракт, 15.09.2026) не срабатывают.
_PHONE = re.compile(r"(?<![\w+.,])(?:\+7|7|8)[\s\- ]*\(?\d{3}\)?[\s\- ]*\d{3}[\s\- ]*\d{2}"
                    r"[\s\- ]*\d{2}(?!\w)(?![.,]\d)|(?<![\w+.,])\+\d{11,14}(?!\w)(?![.,]\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://([^/\s?#\"'<>]+)([^\s\"'<>]*)", re.IGNORECASE)
_TOKEN = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{32,}(?![\w-])")
# Адрес страницы из слов через дефис («…-modeli-2025-goda», «…-purifier-tp7a»): слова — буквы, числа или короткий код
# модели до 12 знаков; у ключа смешанная часть длинная (1.2.1, 1.2.3, 1.2.4).
_SLUG = re.compile(r"(?:[A-Za-z]{1,20}|[0-9]{1,6}|[A-Za-z0-9]{1,12})(?:-(?:[A-Za-z]{1,20}|[0-9]{1,6}|[A-Za-z0-9]{1,12})){3,}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def find_pii(text: str, allowed_domains=()) -> list[str]:
    """Страж 12: что в тексте похоже на ПДн или секрет. Возвращает только виды находок — значение не печатается.
    Ссылка допустима на домен из списка инстанса (или его поддомен) и без строки запроса; адрес-шаблон с именем
    переменной в фигурных скобках — не данные."""
    found = []
    if _PHONE.search(text):
        found.append("телефон")
    if _EMAIL.search(text):
        found.append("адрес почты")
    for match in _URL.finditer(text):
        host, rest = match.group(1).lower(), match.group(2)
        template = "{" in host and "}" in host
        if not template and not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
            found.append("ссылка на домен вне списка инстанса")
        if "?" in rest:
            found.append("строка запроса в ссылке")
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if _UUID.fullmatch(token):
            continue
        if _SLUG.fullmatch(token):
            continue                        # адрес страницы: слова через дефис, каждое — только буквы или только цифры
        if any(char.isdigit() for char in token) and any(char.isalpha() for char in token):
            found.append("похоже на секрет")
            break
    return list(dict.fromkeys(found))


def check_rows_pii(sheet: str, rows, allowed_domains=()) -> None:
    """Проверка строк листа до отправки: находка — стоп стражем 12 с листом, строкой и колонкой, без значения."""
    for position, row in enumerate(rows, start=1):
        for column, cell in row.items():
            kinds = find_pii(cell, allowed_domains) if isinstance(cell, str) else []
            if kinds:
                raise GuardViolation(12, f"«{SHEETS.get(sheet, sheet)}», строка {position}, колонка «{column}»: "
                                         f"{', '.join(kinds)} — запись не отправлена")


# --- шесть операций контракта над бэкендом-таблицей (Х1, Х3, Х8, Х9, Х10) ---

SYSTEM_COLUMNS = ("revision", "updated_at")


class TableBackend(Protocol):
    """Бэкенд-таблица: пять примитивов. Логика контракта живёт в ядре, бэкенд только читает и пишет листы."""

    def header(self, sheet: str) -> list[str] | None:
        """Шапка листа или None, если листа нет — «листа нет» не то же, что «лист пуст» (К9)."""

    def create_sheet(self, sheet: str, columns: list[str]) -> None: ...

    def add_columns(self, sheet: str, columns: list[str]) -> None:
        """Дописать колонки в конец шапки; существующие колонки и колонки людей не двигаются (Х11)."""

    def read_rows(self, sheet: str) -> list[dict]:
        """Строки данных по порядку листа; ключи — имена колонок шапки."""

    def write_rows(self, sheet: str, items: list) -> None:
        """items — пары (позиция строки данных или None — дописать, {колонка: текст}); другие колонки не трогаются."""


@dataclass(frozen=True)
class Snapshot:
    artifacts: dict                  # ключ артефакта → прочитанные и проверенные записи
    missing: tuple[str, ...]         # имена листов, которых в хранилище нет


def _revision(row: dict) -> int:
    text = row.get("revision", "")
    if text == "":
        return 0
    if not isinstance(text, str) or not text.isdigit():
        raise GuardViolation(13, f"ревизия строки {text!r} — не целое число: колонка изменена вне движка")
    return int(text)


def _same(existing: dict, row: dict) -> bool:
    return all(existing.get(column, "") == value for column, value in row.items())


def _unique_numbers(numbers) -> list[Number]:
    """Повторы одного содержимого сливаются; два разных содержимых под одним номером в одной записи — стоп."""
    seen = {}
    for number in numbers:
        key = number_id(number)
        if key in seen and seen[key] != number:
            raise GuardViolation(13, f"у числа {key} в одной записи два разных содержимых — у разных расчётов одной "
                                     "метрики, периода и съёма часть «запрос» источника должна называть расчёт")
        seen.setdefault(key, number)
    return list(seen.values())


class RegistryStore:
    """Шесть операций контракта реестра: создать запись · прочитать по фильтру · обновить статус · добавить замер ·
    связать с деревом · экспортировать снимок; плюс «объявить ожидание» через гейт шума. Каждая запись: ПДн до
    обращения к бэкенду (страж 12), сверка ревизии строки, запись с новой ревизией, чтение обратно (страж 13).
    Строки не удаляются: вместо удаления — статус «архив»."""

    def __init__(self, backend: TableBackend, allowed_domains=(), today=date.today):
        self.backend, self.domains, self.today = backend, tuple(allowed_domains), today

    # --- листы и строки ---

    def _ensure(self, kind: str) -> None:
        sheet, columns = SHEETS[kind], COLUMNS[kind] + SYSTEM_COLUMNS
        header = self.backend.header(sheet)
        if header is None:
            self.backend.create_sheet(sheet, list(columns))
            return
        if header and "id" not in header:
            # Лист людей с тем же именем (в книге инстанса — «Гипотезы» карты v1.0): колонки движка справа от
            # чужой шапки испортили бы его, а строки без номера движок всё равно не прочёл бы.
            raise GuardViolation(13, f"«{sheet}»: лист с этим именем есть, но это не лист движка (нет колонки id) "
                                     "— дать листу людей другое имя или указать другую книгу")
        doubled = sorted({column for column in header if column in columns and header.count(column) > 1})
        if doubled:
            raise GuardViolation(13, f"«{sheet}»: колонки движка повторяются в шапке: {', '.join(doubled)}")
        missing = [column for column in columns if column not in header]
        if missing:
            self.backend.add_columns(sheet, missing)

    def _rows(self, kind: str) -> list[dict]:
        """Строки движка листа; повторённый номер строки — стоп и при чтении, как при записи (Х10)."""
        sheet = SHEETS[kind]
        if self.backend.header(sheet) is None:
            return []
        rows, seen = [], {}
        for position, row in enumerate(self.backend.read_rows(sheet)):
            key = row.get("id", "")
            if key == "":
                continue
            if key in seen:
                raise GuardViolation(13, f"«{sheet}»: номер {decode_text(key)} повторяется в строках листа "
                                         f"{seen[key] + 2} и {position + 2}")
            seen[key] = position
            rows.append(row)
        return rows

    def _index(self, kind: str) -> dict:
        sheet = SHEETS[kind]
        if self.backend.header(sheet) is None:
            return {}
        index = {}
        for position, row in enumerate(self.backend.read_rows(sheet)):
            key = row.get("id", "")
            if key == "":
                continue
            if key in index:
                raise GuardViolation(13, f"«{sheet}»: номер {decode_text(key)} повторяется в строках листа "
                                         f"{index[key][0] + 2} и {position + 2}")
            index[key] = (position, row)
        return index

    def _write(self, kind: str, updates) -> WriteReport:
        """updates — пары (строка движка, ожидаемая ревизия или None для новой записи)."""
        sheet = SHEETS[kind]
        check_rows_pii(kind, [row for row, _ in updates], self.domains)
        self._ensure(kind)
        current, stamp, items = self._index(kind), self.today().isoformat(), []
        for row, expected in updates:
            key = row["id"]
            if key not in current:
                if expected is not None:
                    raise GuardViolation(13, f"«{sheet}»: строки {decode_text(key)} нет — обновлять нечего")
                items.append((None, {**row, "revision": "1", "updated_at": stamp}))
                continue
            position, existing = current[key]
            if expected is None:
                if _same(existing, row):
                    continue
                hint = ("; у разных расчётов одной метрики, периода и съёма часть «запрос» источника должна называть "
                        "расчёт") if kind == "numbers" else ""
                raise GuardViolation(13, f"«{sheet}»: номер {decode_text(key)} уже занят другим содержимым — запись "
                                         f"меняется только операциями обновления{hint}")
            revision = _revision(existing)
            if revision != expected:
                raise GuardViolation(13, f"«{sheet}»: строка {decode_text(key)} изменена другим процессом — ревизия "
                                         f"{revision}, ожидалась {expected}; запись остановлена")
            if not _same(existing, row):
                items.append((position, {**row, "revision": str(revision + 1), "updated_at": stamp}))
        if items:
            self.backend.write_rows(sheet, items)
        back = self._index(kind)
        matched = sum(1 for _, row in items if row["id"] in back and _same(back[row["id"]][1], row))
        return WriteReport(sheet, len(items), matched)

    def _numbers(self) -> dict:
        return numbers_by_id(self._rows("numbers"))

    def _hypothesis(self, hypothesis_id: str) -> tuple[Hypothesis, int]:
        index = self._index("hypotheses")
        key = encode_text(hypothesis_id)
        if key not in index:
            raise GuardViolation(13, f"гипотезы {hypothesis_id} нет на листе «{SHEETS['hypotheses']}»")
        _, row = index[key]
        hypothesis = hypothesis_from_row(row, self._numbers())
        check_state(hypothesis)
        return hypothesis, _revision(row)

    def _store_hypothesis(self, hypothesis: Hypothesis, expected: int | None) -> list[WriteReport]:
        check_state(hypothesis)
        row, numbers = hypothesis_row(hypothesis)
        number_rows = [number_row(number) for number in _unique_numbers(numbers)]
        check_rows_pii("hypotheses", [row], self.domains)
        check_rows_pii("numbers", number_rows, self.domains)
        return [self._write("numbers", [(number, None) for number in number_rows]),
                self._write("hypotheses", [(row, expected)])]

    # --- операции контракта ---

    def create(self, record, class_status: dict | None = None) -> list[WriteReport]:
        """Создать запись: повтор того же содержимого — 0 строк, иное содержимое под тем же номером — стоп (Х3)."""
        if isinstance(record, Number):
            return [self._write("numbers", [(number_row(record), None)])]
        if isinstance(record, Hypothesis):
            return self._store_hypothesis(record, None)
        if isinstance(record, CycleRoute):
            if class_status is None:
                raise GuardViolation(9, f"маршрут {record.cycle_id}: без статусов классов карты источников его не "
                                        "проверить — не записан")
            validate_route(record, class_status)
            return [self._write("routes", [(row, None) for row in route_rows(record)])]
        if isinstance(record, GoalTree):
            record.gap()
            rows, numbers = tree_rows(record)
            number_rows = [number_row(number) for number in _unique_numbers(numbers)]
            check_rows_pii("trees", rows, self.domains)
            check_rows_pii("numbers", number_rows, self.domains)
            return [self._write("numbers", [(number, None) for number in number_rows]),
                    self._write("trees", [(row, None) for row in rows])]
        if isinstance(record, KnowledgeEntry):
            return [self._write("knowledge", [(knowledge_row(record), None)])]
        if isinstance(record, DecisionEntry):
            return [self._write("decisions", [(decision_row(record), None)])]
        if isinstance(record, SourceMapEntry):
            return [self._write("sources", [(source_row(record), None)])]
        raise GuardViolation(13, f"запись типа {type(record).__name__} не входит в семь артефактов контракта")

    def create_numbers(self, numbers) -> WriteReport:
        """Создать записи чисел одной порцией (снимок модели, срез): одна запись и одно чтение обратно на порцию."""
        rows = [number_row(number) for number in _unique_numbers(numbers)]
        return self._write("numbers", [(row, None) for row in rows])

    def create_batch(self, records) -> list[WriteReport]:
        """Создать порцию записей одной записью на лист (перенос, выгрузка). Правила те же, что у create(): повтор того
        же содержимого — 0 строк, иное содержимое под тем же номером — стоп; ПДн проверяются во всей порции до записи.
        Числа пишутся первыми; маршруты и деревья цели проверяются целиком и пишутся только через create()."""
        rows, numbers, seen = {}, [], set()
        for record in records:
            if isinstance(record, Number):
                numbers.append(record)
                continue
            if isinstance(record, Hypothesis):
                check_state(record)
                row, used = hypothesis_row(record)
                kind, numbers = "hypotheses", numbers + list(used)
            elif isinstance(record, KnowledgeEntry):
                kind, row = "knowledge", knowledge_row(record)
            elif isinstance(record, DecisionEntry):
                kind, row = "decisions", decision_row(record)
            elif isinstance(record, SourceMapEntry):
                kind, row = "sources", source_row(record)
            else:
                raise GuardViolation(13, "порцией пишутся числа, гипотезы, знания, решения и источники; "
                                         f"{type(record).__name__} — только через create()")
            if (kind, row["id"]) in seen:
                raise GuardViolation(13, f"«{SHEETS[kind]}»: номер {decode_text(row['id'])} дважды в одной порции")
            seen.add((kind, row["id"]))
            rows.setdefault(kind, []).append(row)
        number_rows = [number_row(number) for number in _unique_numbers(numbers)]
        for kind, kind_rows in rows.items():
            check_rows_pii(kind, kind_rows, self.domains)
        check_rows_pii("numbers", number_rows, self.domains)
        reports = [self._write("numbers", [(row, None) for row in number_rows])] if number_rows else []
        return reports + [self._write(kind, [(row, None) for row in kind_rows]) for kind, kind_rows in rows.items()]

    def ensure_sheets(self) -> list[str]:
        """Завести все семь листов с шапками: лист без строк — «артефакт заведён, не заполнен» (К9). Возвращает имена
        созданных листов; повтор — пустой список."""
        created = []
        for kind, sheet in SHEETS.items():
            if self.backend.header(sheet) is None:
                created.append(sheet)
            self._ensure(kind)
        return created

    def sync_sources(self, entries) -> tuple[WriteReport, tuple[str, ...]]:
        """Выгрузить карту источников снимком (К7): правда — файл карты, лист — копия с датой выгрузки в `updated_at`.
        Новые источники дописываются, изменённые обновляются со сверкой ревизии, одинаковые не пишутся; строки не
        удаляются — имена источников листа, которых в карте нет, возвращаются для отчёта."""
        rows = [source_row(entry) for entry in entries]
        names = [row["id"] for row in rows]
        doubled = sorted({decode_text(name) for name in names if names.count(name) > 1})
        if doubled:
            raise GuardViolation(13, f"карта источников: имена повторяются: {', '.join(doubled)}")
        check_rows_pii("sources", rows, self.domains)
        current = self._index("sources")
        report = self._write("sources", [(row, _revision(current[row["id"]][1]) if row["id"] in current else None)
                                         for row in rows])
        return report, tuple(decode_text(key) for key in current if key not in names)

    def read(self, kind: str, class_status: dict | None = None, verify: bool = True, **where) -> list:
        """Прочитать по фильтру: строки декодируются и проходят доменные проверки (Х10); фильтр — равенство полей.
        verify=False — только декодирование: гейт проверяет каждую запись сам и печатает все нарушения, а не первое."""
        if kind not in SHEETS:
            raise GuardViolation(13, f"артефакта «{kind}» нет среди семи: {', '.join(SHEETS)}")
        rows = self._rows(kind)
        if kind == "numbers":
            items = list(numbers_by_id(rows).values())
        elif kind == "hypotheses":
            numbers = self._numbers()
            items = [hypothesis_from_row(row, numbers) for row in rows]
            for hypothesis in items if verify else ():
                check_state(hypothesis)
        elif kind == "routes":
            items = routes_from_rows(rows)
            if verify and class_status is not None:
                for route in items:
                    validate_route(route, class_status)
        elif kind == "trees":
            items = trees_from_rows(rows, self._numbers())
            for tree in items if verify else ():
                tree.gap()
        elif kind == "knowledge":
            items = [knowledge_from_row(row) for row in rows]
        elif kind == "decisions":
            items = [decision_from_row(row) for row in rows]
        else:
            items = [source_from_row(row) for row in rows]
        return [item for item in items if all(getattr(item, key) == value for key, value in where.items())]

    def update_status(self, hypothesis_id: str, to: HStatus, **changes) -> list[WriteReport]:
        """Обновить статус — только через transition() реестра: все стражи переходов действуют и в хранилище (Х9)."""
        hypothesis, revision = self._hypothesis(hypothesis_id)
        return self._store_hypothesis(transition(hypothesis, to, **changes), revision)

    def declare_expectation(self, hypothesis_id: str, share: Number, change: float, kind: str) -> list[WriteReport]:
        """Объявить ожидаемое изменение через гейт шума (страж 5); статус может уйти в research кодом."""
        hypothesis, revision = self._hypothesis(hypothesis_id)
        return self._store_hypothesis(apply_noise_gate(hypothesis, share, change, kind), revision)

    def add_measurement(self, hypothesis_id: str, measured: Number) -> list[WriteReport]:
        """Добавить замер — через measure(): попадание в порог считает код, второй замер невозможен (Х9)."""
        hypothesis, revision = self._hypothesis(hypothesis_id)
        return self._store_hypothesis(measure(hypothesis, measured), revision)

    def link_to_tree(self, hypothesis_id: str, goal_id: str, branch_id: str) -> list[WriteReport]:
        """Связать гипотезу с веткой дерева цели: номер гипотезы — в список ветки, ветка — в поле гипотезы.

        Гипотеза стоит ровно на одной ветке дерева: при перепривязке номер уходит из прежней ветки той же цели. Иначе
        показ покрытия считал бы её дважды, а прежняя ветка выглядела бы покрытой.
        """
        hypothesis, revision = self._hypothesis(hypothesis_id)
        index = self._index("trees")
        key = encode_text(f"{goal_id}#{branch_id}")
        if key not in index:
            raise GuardViolation(13, f"ветки {branch_id} дерева цели {goal_id} нет на листе «{SHEETS['trees']}»")
        updates = []
        for other_key, (_, row) in index.items():
            if decode_text(_cell(row, "goal_id")) != goal_id:
                continue
            linked = _decode(_cell(row, "hypothesis_ids"), "json_tuple") or ()
            wanted = linked + (hypothesis_id,) if other_key == key and hypothesis_id not in linked else                 tuple(x for x in linked if x != hypothesis_id or other_key == key)
            if wanted != linked:
                branch = {column: row.get(column, "") for column in COLUMNS["trees"]}
                branch["hypothesis_ids"] = _encode(wanted, "json_tuple")
                updates.append((branch, _revision(row)))
        reports = [self._write("trees", updates)] if updates else []
        return reports + self._store_hypothesis(replace(hypothesis, tree_branch=branch_id), revision)

    def note_branch(self, goal_id: str, branch_id: str, reason: str) -> list[WriteReport]:
        """Записать у ветки, почему гипотез на ней нет. Пустая причина стирает запись."""
        index = self._index("trees")
        key = encode_text(f"{goal_id}#{branch_id}")
        if key not in index:
            raise GuardViolation(13, f"ветки {branch_id} дерева цели {goal_id} нет на листе «{SHEETS['trees']}»")
        _, row = index[key]
        branch = {column: row.get(column, "") for column in COLUMNS["trees"]}
        branch["no_hypotheses_reason"] = _encode(reason, "text")
        return [self._write("trees", [(branch, _revision(row))])]

    def export_snapshot(self, class_status: dict | None = None) -> Snapshot:
        """Экспортировать снимок: все семь артефактов, прочитанных с проверками, и список отсутствующих листов."""
        artifacts, missing = {}, []
        for kind, sheet in SHEETS.items():
            if self.backend.header(sheet) is None:
                missing.append(sheet)
                artifacts[kind] = []
            else:
                artifacts[kind] = self.read(kind, class_status=class_status)
        return Snapshot(artifacts, tuple(missing))
