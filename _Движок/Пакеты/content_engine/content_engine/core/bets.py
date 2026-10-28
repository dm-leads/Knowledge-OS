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


def mark_refreshed(instance, topic: dict, url: str, day: str) -> tuple[bool, str]:
    """Правка страницы (Т36.3): новой записи книги нет; у записи страницы — день и тема правки."""
    from .urls import norm_url
    with update(instance) as box:
        record = next((r for r in box["records"] if norm_url(r["url"]) == norm_url(url)), None)
        if record is None:
            return False, "книга ставок: записи страницы нет — правка не отмечена (страница вне книги)"
        done = record.setdefault("refreshed", [])
        if any(x.get("id") == topic["id"] for x in done):
            return False, "книга ставок: правка уже отмечена"
        done.append({"day": day, "id": topic["id"]})
    return True, box["write"] + "; книга ставок: правка отмечена у записи страницы"


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
# множители порога словами; «м» + «лн» собрано из частей — граница пакета ловит это сокращение как сумму компании
SCALES = {"тыс": 1_000, "м" + "лн": 1_000_000}
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
    t = re.sub(r"(за|в течение|через|на)\s+\d+\s*(дн|недел|месяц|мес)\w*", " ", (text or "").lower())
    t = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}(?!\d))", "", t)   # пробел между разрядами — одно число
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(SCALES) + ")?", t)
    if m:
        scale = SCALES.get(m.group(2) or "", 1)
        return float(m.group(1).replace(",", ".")) * scale
    for word, value in _WORD_NUMBERS.items():
        if re.search(rf"(?<!\w){word}(?!\w)", t):    # «один», а не «одинаковый»
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


def decide(record: dict, measured: dict, min_step: int, window: str = "180") -> tuple[str, str]:
    """Итог окна (Т39.2). Ставки нет — «ставки нет». Иначе — сравнение в единице ставки (заявки или деньги): нужны
    данные именно этой единицы ступени 1–2; proxy и «нет данных» — «мало данных». ≥ ожидания — «сыграла»,
    ≤ порога провала — «провал», между — «мало данных», решает человек. Ожидание и порог карточки — за 180 дней:
    на окне 90 оба берутся пополам (темп), иначе статья в темпе выше порога получила бы «провал»."""
    money, leads = measured["money"], measured.get("leads") or {}
    if record.get("bet_missing"):
        if measured["step"] > min(min_step, 2) or money.get("status") in ("нет данных", "proxy"):
            return LOW_DATA, f"ступень {measured['step']}: {money.get('note') or money.get('status')}"
        return NO_BET_RESULT, "ставки нет — деньги страницы записаны, сравнивать не с чем"
    expected, unit, note = expectation_of(record)
    if expected is None:
        return LOW_DATA, "ожидание не числом — решает человек в `bet accept`"
    if measured["step"] > min(min_step, 2):
        return LOW_DATA, f"ступень {measured['step']}: {money.get('note') or money.get('status')}"
    if unit == "money":
        if money.get("status") in ("нет данных", "proxy") or money.get("value") is None:
            return LOW_DATA, f"денег за окно нет: {money.get('note') or money.get('status')}"
        got = money["value"]
    else:
        if leads.get("value") is None or leads.get("status") in ("нет данных", "proxy"):
            return LOW_DATA, "заявок за окно нет — нет замера в единице ставки"
        got = leads["value"]
    threshold = number_in(str(record.get("fail_threshold") or ""))
    if str(window) == "90":
        expected = expected / 2
        threshold = None if threshold is None else threshold / 2
        note = (note + "; " if note else "") + "окно 90 — половина ожидания и порога за 180"
    if got >= expected:
        return PLAYED, f"{got:g} ≥ ожидания {expected:g}" + (f" ({note})" if note else "")
    if threshold is not None and got <= threshold:
        return FAILED, f"{got:g} ≤ порога провала {threshold:g}" + (f" ({note})" if note else "")
    if threshold is None:
        return LOW_DATA, f"{got:g} < ожидания {expected:g}, порог провала не числом — решает человек"
    return LOW_DATA, f"{got:g} между порогом {threshold:g} и ожиданием {expected:g} — решает человек в `bet accept`"
