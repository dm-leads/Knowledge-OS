"""Ставка темы из гипотезы Движка роста (Б1, Т26): только чтение, своим кодом, без импорта `growth_engine` (Р2).

Два вида источника (`sources.growth.kind`):
- `markdown` — папка хранилища Движка роста в режиме «файл Markdown»: лист гипотез `Гипотезы.md` и лист чисел
  `Модель — снимки.md`; ожидание — по ссылкам `effect_goal_units_id` и `effect_rub_id` на лист чисел. Ячейки
  закодированы, как пишет Движок: HTML-сущности `&amp;`, `&#124;`, `&#10;`, `&#13;` и ведущий апостроф у текста,
  который таблица приняла бы за формулу;
- `file` — папка карточек `<id>.yaml` по шаблону пакета `growth_card.yaml`. Карточку выгружает агент из сервера
  аналитики проекта (MCP) или заполняет с владельцем у проекта без своей аналитики — временное решение до своего
  Движка. У гипотез направлений статей ожидание и порог провала записаны текстом формулировки, а ставка — на
  направление из нескольких статей: поэтому в карточке `unit` и `planned_count`, а числа, выписанные агентом из
  формулировки, — со статусом «оценка» и источником.
Ни одного запроса записи: Движок роста меняет статусы сам, машина держит зеркало ставки в очереди и книге ставок.

Решение по гипотезе (волна 3, Б1): `decision()` читает решение Движка — `scale`, `iterate`, `kill`, `research` (его
закрытие цикла пишет поле `decision` строки гипотезы). У карточки файлом — необязательные поля `decision` и
`decided_at`; карточка старше 30 дней решения не даёт (устарела — выгрузить заново).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from ..core.errors import EngineError, GateRefused, SourceError

ARCHIVE = ("архив", "archive", "archived")
NUMBER_STATUSES = ("факт", "оценка", "proxy", "нет данных")
CARD_REQUIRED = ("id", "formulation", "status", "exported_at", "exported_by", "unit", "planned_count",
                 "expectation", "fail_threshold", "windows", "expectation_numbers")
NUMBER_REQUIRED = ("value", "unit", "cabinet", "period", "denominator", "source", "as_of", "status")
CARD_MAX_AGE = 30
DECISIONS = ("scale", "iterate", "kill", "research")
_ENTITIES = (("&", "&amp;"), ("|", "&#124;"), ("\n", "&#10;"), ("\r", "&#13;"))
HYPOTHESES, NUMBERS = "Гипотезы", "Модель — снимки"


def _unescape(text: str) -> str:
    for raw, entity in reversed(_ENTITIES):
        text = text.replace(entity, raw)
    return text


def _decode(cell: str) -> str:
    return cell[1:] if cell.startswith("'") else cell


def read_sheet(path: Path) -> list[dict]:
    """Лист хранилища Markdown: таблица `| … |` с шапкой; ширина строки — как у шапки."""
    if not path.is_file():
        raise SourceError(f"листа нет: {path}")
    table = [line for line in path.read_text(encoding="utf-8").split("\n") if line.startswith("|")]
    if len(table) < 2:
        raise SourceError(f"{path.name}: нет таблицы с шапкой")

    def cells(line):
        inner = line[1:-1] if line.endswith("|") else line[1:]
        return [_decode(_unescape(c[1:-1] if len(c) >= 2 and c.startswith(" ") and c.endswith(" ") else c))
                for c in inner.split("|")]

    header = cells(table[0])
    rows = []
    for n, line in enumerate(table[2:], 1):
        row = cells(line)
        if len(row) != len(header):
            raise SourceError(f"{path.name}: строка {n} шириной {len(row)}, а шапка — {len(header)}")
        rows.append(dict(zip(header, row)))
    return rows


def _number_line(n: dict) -> str:
    return (f"{n['value']} {n['unit']} · кабинет {n['cabinet'] or '—'} · период {n['period']} · знаменатель "
            f"{n['denominator'] or '—'} · источник {n['source']} · снято {n['as_of']} · статус {n['status']}")


class MarkdownGrowth:
    kind = "markdown"

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.reads = 0
        self.writes = 0

    def _number(self, numbers: dict, ref: str, hid: str, field: str) -> dict | None:
        if not ref:
            return None
        row = numbers.get(ref)
        if row is None:
            raise EngineError(f"гипотеза {hid}: поле {field} ссылается на число {ref}, а на листе «{NUMBERS}» его нет")
        status = {"fact": "факт", "estimate": "оценка", "no_data": "нет данных"}.get(row["status"], row["status"])
        return {"value": float(row["value"]) if row["value"] else None, "unit": row.get("unit", ""),
                "cabinet": row.get("scope", ""), "period": f"{row['period_start']}–{row['period_end']}",
                "denominator": row.get("denominator", ""), "source": row["source"], "as_of": row["as_of"],
                "status": status}

    def bet(self, hid: str) -> dict:
        self.reads += 1
        rows = read_sheet(self.folder / f"{HYPOTHESES}.md")
        row = next((r for r in rows if r.get("id") == hid), None)
        if row is None:
            raise EngineError(f"гипотезы {hid} нет на листе «{HYPOTHESES}» ({self.folder})")
        if row.get("status", "").lower() in ARCHIVE:
            raise GateRefused(f"гипотеза {hid} в архиве — ставку из неё не берут")
        numbers = {r["id"]: r for r in read_sheet(self.folder / f"{NUMBERS}.md")}
        expectation = [n for n in (self._number(numbers, row.get("effect_goal_units_id", ""), hid,
                                                "effect_goal_units_id"),
                                   self._number(numbers, row.get("effect_rub_id", ""), hid, "effect_rub_id")) if n]
        window = int(row["window_days"]) if row.get("window_days") else None
        return {"hypothesis_id": hid, "hypothesis": row["formulation"],
                "expectation": "; ".join(_number_line(n) for n in expectation) or "не посчитано Движком",
                "expectation_numbers": expectation, "fail_threshold": row.get("failure_criterion", ""),
                "windows": [window] if window else [90, 180], "unit": "article", "planned_count": 1,
                "notes": []}


    def decision(self, hid: str) -> dict:
        self.reads += 1
        row = next((r for r in read_sheet(self.folder / f"{HYPOTHESES}.md") if r.get("id") == hid), None)
        if row is None:
            raise EngineError(f"гипотезы {hid} нет на листе «{HYPOTHESES}» ({self.folder})")
        value = (row.get("decision") or "").strip().lower()
        return {"decision": value if value in DECISIONS else "", "decided_at": (row.get("updated_at") or "")[:10],
                "note": ""}


def validate_card(card: dict, name: str) -> None:
    if not isinstance(card, dict):
        raise EngineError(f"карточка {name}: верхний уровень — не раздел")
    missing = [k for k in CARD_REQUIRED if k not in card]
    if missing:
        raise EngineError(f"карточка {name}: нет полей {missing}")
    if card["unit"] not in ("direction", "article"):
        raise EngineError(f"карточка {name}: unit — direction или article")
    if not isinstance(card["planned_count"], int) or card["planned_count"] < 1:
        raise EngineError(f"карточка {name}: planned_count — целое ≥ 1")
    if not str(card["expectation"]).strip() or not str(card["fail_threshold"]).strip():
        raise EngineError(f"карточка {name}: expectation и fail_threshold не пусты — без них замер не с чем сравнить")
    if not isinstance(card["windows"], list) or not all(isinstance(w, int) and w > 0 for w in card["windows"]):
        raise EngineError(f"карточка {name}: windows — список дней, например [90, 180]")
    for i, n in enumerate(card["expectation_numbers"] or []):
        lost = [k for k in NUMBER_REQUIRED if k not in n]
        if lost:
            raise EngineError(f"карточка {name}: expectation_numbers[{i}] без полей {lost}")
        if n["status"] not in NUMBER_STATUSES:
            raise EngineError(f"карточка {name}: expectation_numbers[{i}].status — одно из {NUMBER_STATUSES}")
    decision = str(card.get("decision") or "").strip().lower()
    if decision and decision not in DECISIONS:
        raise EngineError(f"карточка {name}: decision — одно из {DECISIONS} или пусто")


class FileGrowth:
    kind = "file"

    def __init__(self, folder: Path, today: date):
        self.folder = Path(folder)
        self.today = today
        self.reads = 0
        self.writes = 0

    def bet(self, hid: str) -> dict:
        self.reads += 1
        path = self.folder / f"{hid}.yaml"
        if not path.is_file():
            raise EngineError(f"карточки гипотезы нет: {path} — выгрузите её (шаблон: `content-engine queue card`)")
        card = yaml.safe_load(path.read_text(encoding="utf-8"))
        validate_card(card, path.name)
        if str(card["id"]) != hid:
            raise EngineError(f"карточка {path.name}: id {card['id']} ≠ {hid}")
        if str(card["status"]).lower() in ARCHIVE:
            raise GateRefused(f"гипотеза {hid} в архиве — ставку из неё не берут")
        notes = []
        exported = date.fromisoformat(str(card["exported_at"])[:10])
        if (self.today - exported).days > CARD_MAX_AGE:
            notes.append(f"карточка {hid} выгружена {exported} — старше {CARD_MAX_AGE} дней, выгрузите заново")
        return {"hypothesis_id": hid, "hypothesis": card["formulation"], "expectation": str(card["expectation"]),
                "expectation_numbers": card["expectation_numbers"] or [], "fail_threshold": str(card["fail_threshold"]),
                "windows": card["windows"], "unit": card["unit"], "planned_count": card["planned_count"],
                "notes": notes}


    def decision(self, hid: str) -> dict:
        """Решение Движка из карточки; устаревшая карточка решения не даёт — его нельзя применять вслепую."""
        self.reads += 1
        path = self.folder / f"{hid}.yaml"
        if not path.is_file():
            raise EngineError(f"карточки гипотезы нет: {path}")
        card = yaml.safe_load(path.read_text(encoding="utf-8"))
        validate_card(card, path.name)
        exported = date.fromisoformat(str(card["exported_at"])[:10])
        if (self.today - exported).days > CARD_MAX_AGE:
            return {"decision": "", "decided_at": "",
                    "note": f"карточка {hid} выгружена {exported} — старше {CARD_MAX_AGE} дней, решение не применяется"}
        return {"decision": str(card.get("decision") or "").strip().lower(),
                "decided_at": str(card.get("decided_at") or ""), "note": ""}


def source(instance):
    g = instance.p["sources"]["growth"]
    if g["kind"] == "none":
        return None
    folder = Path(g["path"])
    folder = folder if folder.is_absolute() else instance.root / folder
    if g["kind"] == "markdown":
        return MarkdownGrowth(folder)
    return FileGrowth(folder, instance.today)


def card_template() -> str:
    from importlib import resources
    return resources.files("content_engine.templates").joinpath("growth_card.yaml").read_text(encoding="utf-8")
