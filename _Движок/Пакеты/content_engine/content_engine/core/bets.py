"""Книга ставок `data/bets.json` (Т23.1, Т39): статья → ставка → день выкладки → замеры 90 и 180 дней → итог.

Запись в день выкладки делает «доехало» (`add`); замер и итог — волна 3 (`steps/bets_cmd.py`). Книгу пишут и машина
по расписанию, и чат контента при выпуске — поэтому каждая запись идёт под блокировкой `bets/write` с перечитыванием
файла внутри неё (`update`): параллельная запись получает отказ, а не стирает чужую строку.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date, timedelta

from . import locks, store

WINDOWS = (90, 180)


def load(instance) -> list[dict]:
    return store.read_json(instance.file("bets"), "bets", missing_ok=True)["records"]


@contextmanager
def update(instance):
    """Записи книги под блокировкой: внутри — свежие строки, на выходе — запись со сверкой."""
    with locks.held(instance, "bets", "write"):
        records = load(instance)
        box = {"records": records, "write": ""}
        yield box
        box["write"] = store.write_json(instance.file("bets"), "bets", box["records"])


def add(instance, topic: dict, url: str, day: str) -> tuple[bool, str]:
    """Добавить ставку темы, если её ещё нет. Вернуть (добавлена ли, строка сверки)."""
    with update(instance) as box:
        added, text = _add(instance, box["records"], topic, url, day)
    return added, (box["write"] + text if added else text)


def _add(instance, records: list, topic: dict, url: str, day: str) -> tuple[bool, str]:
    from .urls import norm_url
    if any(r["id"] == topic["id"] for r in records):
        return False, "книга ставок: запись уже есть"
    # статья, заведённая задним числом по корпусу, получает ставку темы вместо второй записи (ключ — адрес)
    old = next((r for r in records if r.get("origin") == "corpus" and norm_url(r["url"]) == norm_url(url)), None)
    if old is not None:
        records.remove(old)
        # замеры записи задним числом не теряются; при настоящей ставке это история — окна замеряются по ставке заново
        carried = {("measures_corpus" if not bet_missing(topic) else "measures"): old["measures"]}             if old.get("measures") else {}
    else:
        carried = {}
    bet = topic.get("bet") or {}
    published = date.fromisoformat(day)
    missing = bet_missing(topic)
    records.append({"id": topic["id"], "topic": topic["topic"], "url": url, "hypothesis": bet.get("hypothesis", ""),
                    "hypothesis_id": bet.get("hypothesis_id", ""), "expectation": bet.get("expectation", ""),
                    "fail_threshold": bet.get("fail_threshold", ""), "published": day,
                    "measure": {str(w): (published + timedelta(days=w)).isoformat() for w in WINDOWS},
                    "result": None, "bet_missing": missing, "direction": topic.get("direction", ""),
                    **{k: bet[k] for k in ("expectation_numbers", "unit", "planned_count") if k in bet}, **carried})
    return True, f"; {NO_BET}" if missing else ""


NO_BET = "ставки нет — замер через 90 дней не с чем сравнить: `queue bet <id> --hypothesis-id …`"


def bet_missing(topic: dict) -> bool:
    """Н4: тема без ставки — книга ставок замерит деньги, но сравнить их будет не с чем."""
    bet = topic.get("bet") or {}
    return not (str(bet.get("hypothesis") or "").strip() and str(bet.get("expectation") or "").strip())


# --- замер и итог (волна 3, Т39) ---

PLAYED, FAILED, LOW_DATA, NO_BET_RESULT, NOT_YET = "сыграла", "провал", "мало данных", "ставки нет", "окно не наступило"
RESULTS = (PLAYED, FAILED, LOW_DATA)
_WORD_NUMBERS = {"ноль": 0.0, "нуль": 0.0, "ни одной": 0.0, "ни одного": 0.0, "одна": 1.0, "один": 1.0}


def unit_of(text: str) -> str:
    """Единица ставки: заявки или деньги; иначе пусто."""
    t = (text or "").lower()
    if any(w in t for w in ("заяв", "лид", "lead")):
        return "leads"
    if any(w in t for w in ("₽", "руб", "rub", "выручк", "деньг")):
        return "money"
    return ""


def number_in(text: str) -> float | None:
    """Первое число строки порога («0 за 180 дней», «ноль заявок»); нет — None."""
    t = (text or "").lower()
    m = re.search(r"\d+(?:[.,]\d+)?", t)
    if m:
        return float(m.group(0).replace(",", "."))
    for word, value in _WORD_NUMBERS.items():
        if word in t:
            return value
    return None


def expectation_of(record: dict) -> tuple[float | None, str, str]:
    """(ожидание на статью, единица, примечание). Ставка на направление делится на число статей."""
    for n in record.get("expectation_numbers") or []:
        unit = unit_of(str(n.get("unit", "")))
        if unit and n.get("value") is not None:
            value = float(n["value"])
            if record.get("unit") == "direction" and (record.get("planned_count") or 1) > 1:
                return value / record["planned_count"], unit, f"ставка направления: {n['value']} / " \
                                                              f"{record['planned_count']} статей"
            return value, unit, ""
    return None, "", "ожидание не числом"


def decide(record: dict, measured: dict, min_step: int) -> tuple[str, str]:
    """Итог окна (Т39.2): ступень ниже порога данных или «нет данных» — «мало данных»; ставки нет — «ставки нет»;
    иначе сравнение в единице ставки: ≥ ожидания — «сыграла», ≤ порога провала — «провал», между — «мало данных»."""
    money, leads = measured["money"], measured.get("leads")
    if measured["step"] > min(min_step, 2) or money.get("status") in ("нет данных", "proxy"):
        return LOW_DATA, f"ступень {measured['step']}: {money.get('note') or money.get('status')}"
    if record.get("bet_missing"):
        return NO_BET_RESULT, "ставки нет — деньги страницы записаны, сравнивать не с чем"
    expected, unit, note = expectation_of(record)
    if expected is None:
        return LOW_DATA, "ожидание не числом — решает человек в `bet accept`"
    got = money["value"] if unit == "money" else (leads or {}).get("value")
    if got is None:
        return LOW_DATA, f"нет замера в единице ставки ({'заявки' if unit == 'leads' else 'деньги'})"
    threshold = number_in(str(record.get("fail_threshold") or ""))
    if got >= expected:
        return PLAYED, f"{got:g} ≥ ожидания {expected:g}" + (f" ({note})" if note else "")
    if threshold is not None and got <= threshold:
        return FAILED, f"{got:g} ≤ порога провала {threshold:g}"
    if threshold is None:
        return LOW_DATA, f"{got:g} < ожидания {expected:g}, порог провала не числом — решает человек"
    return LOW_DATA, f"{got:g} между порогом {threshold:g} и ожиданием {expected:g} — решает человек в `bet accept`"
