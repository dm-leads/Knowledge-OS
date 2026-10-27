"""Вопросы клиентов как источник спроса (А1, Т31.1): CSV `question;count` из речевой аналитики проекта.

Только агрегат: текст вопроса и число обращений, без имён, телефонов, почты и цитат. Колонка, кроме этих двух, —
ошибка; значение, похожее на телефон, почту или ссылку, — ошибка с номером строки, файл не читается (по образцу стража
персональных данных Движка роста, своим кодом). Правила «похоже на имя» нет намеренно: на названиях брендов и моделей
оно ложно срабатывает; имена держит источник — выгрузка агрегатом без цитат.

Вопрос, на который корпус уже отвечает (слой 2 гейта темы — «пересекается» или «спорно»), идёт в отчёт «уже
отвечено: <адрес>»; остальные — кандидаты спроса с числом обращений (факт, источник — речевая аналитика проекта).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from ..core.errors import EngineError, SourceError
from ..core.number import FACT
from .demand import demand_row

PII = (("телефон", re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b")),
       ("почта", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
       ("ссылка", re.compile(r"https?://|www\.", re.I)))
COLUMNS = ["question", "count"]


class ClientQuestions:
    name = "client_questions"

    def __init__(self, path: Path, as_of: str, answered=None):
        self.path = Path(path)
        self.as_of = as_of
        self.answered = answered or (lambda q: None)
        self.already: list[tuple[str, str]] = []

    def read(self) -> list[tuple[str, int]]:
        if not self.path.is_file():
            raise SourceError(f"файла вопросов клиентов нет: {self.path}")
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter=";")
            header = [c.strip().lower() for c in next(reader, [])]
            if header != COLUMNS:
                raise EngineError(f"{self.path.name}: колонки {header}, нужны ровно {COLUMNS} — других полей (имя, "
                                  "телефон, цитата) в выгрузке быть не должно")
            rows = []
            for n, row in enumerate(reader, 2):
                if not row or not row[0].strip():
                    continue
                for kind, rx in PII:
                    if any(rx.search(cell) for cell in row):
                        raise EngineError(f"{self.path.name}, строка {n}: похоже на {kind} — персональные данные в "
                                          "выгрузку не идут, файл не читается")
                try:
                    count = int(float(row[1].replace(",", ".")))
                except (IndexError, ValueError) as exc:
                    raise EngineError(f"{self.path.name}, строка {n}: число обращений не число") from exc
                rows.append((row[0].strip(), count))
        return rows

    def fetch(self, period_from: str, period_to: str) -> list[dict]:
        out = []
        for question, count in self.read():
            page = self.answered(question)
            if page:
                self.already.append((question, page))
                continue
            out.append(demand_row(question, "client_questions:речевая аналитика", period_from, period_to, self.as_of,
                                  asks=(count, FACT)))
        return out


def answered_by_corpus(instance):
    """Вопрос закрыт корпусом, если слой 2 гейта темы его находит («пересекается» или «спорно»)."""
    from ..steps.dedup import PASS, layer_texts

    def check(question: str) -> str | None:
        result = layer_texts(instance.index(), [question], instance.p["thresholds"])
        return None if result.outcome == PASS else (result.page or "корпус")
    return check
