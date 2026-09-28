"""Движок цикла (слой 4): скоринг гипотезы, порог шума гипотезы, RAT, доказательства из диалогов.

Стражи: 5 — эффект меньше шума → research кодом; 8 — эффект в ₽ только из модели; 9 — подфакторы без молчаливых
допущений; 10 — доказательства собирает код, несуществующие номера вычёркиваются.
Модуль не знает имён систем, кабинетов и полей источников.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

from .economy import noise
from .errors import GuardViolation
from .number import Number
from .registry import CHANGE_ABSOLUTE, CHANGE_KINDS, COUNT_UNITS, MODEL_SYSTEM, HStatus, Hypothesis, transition

CREDIBILITY_FACTORS = ("сила факта", "ясность проблемы", "близость к ключевой метрике", "job-fit", "прецедент",
                       "измеримость")
EFFORT_FACTORS = ("ресурсы", "техника", "операции", "данные и трекинг", "риск")
SCALE = range(1, 6)


@dataclass(frozen=True)
class Priority:
    credibility: float        # вера: среднее подфакторов ÷ 5, от 0,2 до 1
    effort: float             # сложность: среднее подфакторов, от 1 до 5
    value: float | None       # эффект ₽ × вера ÷ сложность — относительный балл для сортировки, не деньги
    note: str


def _mean(what: str, given: dict, names) -> float:
    if set(given) != set(names):
        missing = [name for name in names if name not in given]
        extra = [name for name in given if name not in names]
        raise GuardViolation(9, f"{what}: подфакторы не по канону — нет {missing}, лишние {extra}")
    for name, value in given.items():
        if isinstance(value, bool) or not isinstance(value, int) or value not in SCALE:
            raise GuardViolation(9, f"{what}: «{name}» = {value!r} — нужна шкала целых от 1 до 5")
    return sum(given.values()) / len(names)


def score(credibility: dict, effort: dict, effect_rub: Number | None = None) -> Priority:
    """ПРИОРИТЕТ = эффект ₽ × вера ÷ сложность. Вера — среднее шести подфакторов ÷ 5, сложность — среднее пяти.
    Приоритет — относительный балл для сортировки; без эффекта в ₽ из модели он не считается (страж 8)."""
    believed = _mean("вера", credibility, CREDIBILITY_FACTORS) / 5
    hard = _mean("сложность", effort, EFFORT_FACTORS)
    if effect_rub is None:
        return Priority(believed, hard, None, "эффект в ₽ не посчитан — приоритет не считается")
    if effect_rub.source_system != MODEL_SYSTEM or effect_rub.unit in COUNT_UNITS:
        raise GuardViolation(8, f"эффект «{effect_rub.metric}» не из модели или не в деньгах — приоритет не считается")
    if effect_rub.value is None:
        return Priority(believed, hard, None, "эффект в ₽ — нет данных, приоритет не считается")
    return Priority(believed, hard, effect_rub.value * believed / hard,
                    f"относительный балл, не деньги; эффект — {effect_rub.status.value}")


def _points(share: float) -> str:
    return f"{share * 100:.2f}".replace(".", ",")


GATE_STATUSES = (HStatus.IDEA, HStatus.RESEARCH, HStatus.CANDIDATE, HStatus.BLOCKED_NO_DATA)


def apply_noise_gate(h: Hypothesis, share: Number, change: float, kind: str) -> Hypothesis:
    """Страж 5: порог шума главной доли считает движок экономики; ожидаемое изменение меньше порога — research кодом.
    Вид изменения объявляется явно (страж 9): абсолютное — в долях (0,01 = 1 п.п.), относительное — доля от текущего
    значения (0,10 = +10%), в п.п. его переводит код. Порог здесь — на базе доли; при запуске теста реестр пересчитывает
    его из той же доли на ожидаемом N теста. Объявленное до запуска после запуска не переписывается (страж 7)."""
    if h.status not in GATE_STATUSES:
        raise GuardViolation(7, f"гипотеза {h.id}: статус «{h.status.value}» — ожидание и порог объявляются до запуска "
                                "и не переписываются")
    if kind not in CHANGE_KINDS:
        raise GuardViolation(9, f"гипотеза {h.id}: вид изменения {kind!r} не объявлен — нужно "
                                f"{' или '.join(CHANGE_KINDS)}")
    if share.metric != h.main_metric:
        raise GuardViolation(5, f"гипотеза {h.id}: доля «{share.metric}» — не главная метрика «{h.main_metric}»")
    if not _finite(change):
        raise GuardViolation(5, f"гипотеза {h.id}: ожидаемое изменение {change!r} — нужно конечное число")
    p = share.value if share.value is not None and 0 <= share.value <= 1 else None
    expected = change if kind == CHANGE_ABSOLUTE else (p * change if p is not None else None)
    if expected is not None and (abs(expected) > 1 or (p is not None and not 0 <= p + expected <= 1)):
        raise GuardViolation(5, f"гипотеза {h.id}: ожидаемая доля {(p or 0) + expected:g} вне [0; 1] или изменение по "
                                "модулю больше 1 — изменение записано не в долях")
    threshold = noise(share)
    stamped = replace(h, expected_delta=expected, expected_kind=kind, noise_share=share, noise_threshold=threshold)
    if expected is None:
        reason = "доля не определена — относительное изменение не переводится в п.п. (страж 5)"
    elif threshold.value is None:
        reason = f"порог шума не определён: {threshold.missing} (страж 5)"
    elif abs(expected) < threshold.value:
        reason = (f"ожидаемое изменение {_points(expected)} п.п. меньше порога шума "
                  f"{_points(threshold.value)} п.п. (страж 5)")
    else:
        return stamped
    if h.status is HStatus.RESEARCH:
        return replace(stamped, status_reason=reason)
    return transition(stamped, HStatus.RESEARCH, status_reason=reason)


MEASURABLE = (HStatus.IN_TEST, HStatus.DEFERRED)


def measure(h: Hypothesis, measured: Number) -> Hypothesis:
    """Замер теста (Х9) — единственный путь в статус «замер». Замер — та же доля, что у гейта шума (метрика, кабинет,
    поток, разрез, единица, система), за объявленное окно от даты старта, снятый не раньше конца окна (у отложенного
    замера — не раньше его даты). Попал ли в порог — считает код: изменение от доли гейта того же знака, что ожидание,
    и не меньше объявленного до старта порога (страж 7)."""
    if h.status not in MEASURABLE:
        raise GuardViolation(7, f"гипотеза {h.id}: замер добавляется только к гипотезе «в тесте» или «отложенный "
                                f"замер», а статус — «{h.status.value}»")
    base = h.noise_share
    if base is None or h.expected_delta is None or h.threshold is None or h.start_date is None or not h.window_days:
        raise GuardViolation(7, f"гипотеза {h.id}: до старта не объявлены доля, ожидание, порог или окно")
    if (measured.metric, measured.scope, measured.flow, measured.segment, measured.unit) != \
            (base.metric, base.scope, base.flow, base.segment, base.unit):
        raise GuardViolation(7, f"гипотеза {h.id}: замер «{measured.metric}» ({measured.scope}, {measured.flow}) — не та "
                                f"доля, что объявлена до старта: «{base.metric}» ({base.scope}, {base.flow})")
    if (measured.source_class, measured.source_system) != (base.source_class, base.source_system):
        raise GuardViolation(4, f"гипотеза {h.id}: замер из «{measured.source_system}», а доля гейта — из "
                                f"«{base.source_system}»: смешанная метрика")
    window_end = h.start_date + timedelta(days=h.window_days)
    if (measured.period_start, measured.period_end) != (h.start_date, window_end):
        raise GuardViolation(7, f"гипотеза {h.id}: окно замера {measured.period_start:%d.%m.%Y}–"
                                f"{measured.period_end:%d.%m.%Y} не совпадает с объявленным {h.start_date:%d.%m.%Y}–"
                                f"{window_end:%d.%m.%Y}")
    ready = max(window_end, h.deferred_until) if h.status is HStatus.DEFERRED and h.deferred_until else window_end
    if measured.as_of is None or measured.as_of < ready:
        raise GuardViolation(7, f"гипотеза {h.id}: замер снят раньше {ready:%d.%m.%Y} — окно или отложенный срок не "
                                "закончились")
    if measured.value is None:
        raise GuardViolation(7, f"гипотеза {h.id}: у замера нет значения — {measured.missing or 'нет данных'}")
    delta = measured.value - base.value
    in_threshold = delta != 0 and (delta > 0) == (h.expected_delta > 0) and abs(delta) >= h.threshold
    reason = (f"изменение {_points(delta)} п.п. от доли гейта, порог ± {_points(h.threshold)} п.п. — "
              f"{'в пороге' if in_threshold else 'не в пороге'}")
    return transition(h, HStatus.MEASURED, measured=measured, in_threshold=in_threshold, status_reason=reason)


@dataclass(frozen=True)
class Assumption:
    statement: str            # допущение в утвердительной форме: «сегмент платит за монтаж», а не «могут не платить»
    p_wrong: float            # вероятность, что допущение ложно, — от 0 до 1
    cost_of_error: float      # цена, если допущение ложно
    cost_of_check: float      # цена самой дешёвой проверки — в той же единице, что и цена ошибки
    removable: bool = False   # допущение можно убрать из стека — оно идёт первым


def _finite(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def rat_priority(p: float, cost_of_error: float, cost_of_check: float) -> float:
    """RAT: P × цена ошибки ÷ цена проверки. Формула упорядочивает допущения, а не оценивает их."""
    if not _finite(p) or not 0 <= p <= 1:
        raise GuardViolation(9, f"RAT: вероятность {p!r} — нужна доля от 0 до 1")
    if not _finite(cost_of_error) or cost_of_error < 0:
        raise GuardViolation(9, f"RAT: цена ошибки {cost_of_error!r} — нужно конечное число не меньше нуля")
    if not _finite(cost_of_check) or cost_of_check <= 0:
        raise GuardViolation(9, f"RAT: цена проверки {cost_of_check!r} — нужно конечное число больше нуля")
    return p * cost_of_error / cost_of_check


def order_assumptions(assumptions) -> tuple[Assumption, ...]:
    """Порядок проверки: сначала допущения, которые можно убрать (убрать дешевле любой проверки), дальше — по
    убыванию приоритета RAT; при равном приоритете сохраняется порядок записи."""
    scored = []
    for a in assumptions:
        if not a.statement.strip():
            raise GuardViolation(9, "RAT: допущение без формулировки")
        scored.append((a, rat_priority(a.p_wrong, a.cost_of_error, a.cost_of_check)))
    return tuple(a for a, _ in sorted(scored, key=lambda pair: (not pair[0].removable, -pair[1])))


_FACT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True)
class Fragment:
    id: str                   # номер факта речевой аналитики
    channel: str              # канал беседы
    conversation: str         # идентификатор беседы в базе диалогов
    on: date | None           # дата беседы; без даты фрагмент вычёркивается
    code: str                 # код факта
    value: str                # значение факта
    quote: str                # очищенная цитата; сырая не читается
    note: str = ""            # оговорка источника, например о привязке к соседней беседе


@dataclass(frozen=True)
class Evidence:
    fragments: tuple[Fragment, ...]
    struck: tuple[tuple[str, str], ...]   # (номер, почему вычеркнут)


def _fact_id(raw) -> str | None:
    """Номер факта — uuid строки факта в канонической записи; регистр и пробелы по краям не важны."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    return text if _FACT_ID.fullmatch(text) else None


@dataclass(frozen=True)
class Unavailable:
    id: str                   # номер факта, который источник знает, но не отдаёт фрагментом
    reason: str               # почему: код вне белого списка, нет связи с беседой


NOT_FOUND = "нет в базе диалогов"


def _plain_text(text) -> str:
    return " ".join(str(text).split())


def _mismatch(fragment: Fragment | None, code: str, value: str | None, period) -> str:
    """Почему фрагмент не доказывает утверждение; пустая строка — доказывает."""
    if fragment is None:
        return NOT_FOUND
    if fragment.code != code:
        return f"код факта «{fragment.code}», а утверждение — про «{code}»"
    if value is not None and _plain_text(fragment.value) != _plain_text(value):
        return f"значение «{fragment.value}», а утверждение — про «{value}»"
    if fragment.on is None:
        return "нет даты беседы"
    if period is not None and not period[0] <= fragment.on < period[1]:
        return f"беседа {fragment.on:%d.%m.%Y} вне периода утверждения"
    if not fragment.quote.strip():
        return "нет очищенной цитаты"
    return ""


def build_evidence(ids, fetch, code: str, value: str | None = None,
                   period: tuple[date, date] | None = None) -> Evidence:
    """Страж 10: доказательства собирает код по номерам фактов для утверждения «код факта = значение за период»;
    `fetch(номера) → фрагменты и недоступные номера` — метод источника. Номер не по формату, отсутствующий в базе,
    недоступный у источника (с его причиной), не про это утверждение (код, значение, период), без даты беседы или без
    очищенной цитаты вычёркивается и перечисляется; то, чего не просили, — стоп."""
    if not isinstance(code, str) or not code.strip():
        raise GuardViolation(10, "доказательство без утверждения: не задан код факта")
    wanted, struck = [], []
    for raw in ids:
        key = _fact_id(raw)
        if key is None:
            struck.append((str(raw), "не номер факта"))
        elif key not in wanted:
            wanted.append(key)
    found = {}
    for item in (fetch(wanted) if wanted else []):
        if item.id not in wanted:
            raise GuardViolation(10, "источник вернул номер, которого не просили, — доказательства не собраны")
        found[item.id] = item
    fragments = []
    for key in wanted:
        item = found.get(key)
        if isinstance(item, Unavailable):
            reason = item.reason.strip() or "источник не отдал фрагмент"
        else:
            reason = _mismatch(item, code, value, period)
        if reason:
            struck.append((key, reason))
        else:
            fragments.append(item)
    return Evidence(tuple(fragments), tuple(struck))
