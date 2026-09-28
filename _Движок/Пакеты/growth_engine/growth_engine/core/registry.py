"""Контракт реестра гипотез: поля по трём моментам, статусы и переходы (стражи 5, 6, 7, 8, 14)."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

from .errors import GuardViolation
from .ladders import UnitOfAction
from .number import SOURCE_CLASSES, Number, Status


class HStatus(str, Enum):
    IDEA = "идея"
    RESEARCH = "research"
    CANDIDATE = "candidate"
    IN_TEST = "в тесте"
    MEASURED = "замер"
    CONCLUDED = "вывод"
    ARCHIVE = "архив"
    WAITING_OWNER = "ждёт владельца"
    BLOCKED_NO_DATA = "blocked: нет данных"
    DEFERRED = "отложенный замер"


class Decision(str, Enum):
    SCALE = "scale"
    ITERATE = "iterate"
    KILL = "kill"
    RESEARCH = "research"


ZONE_OWN = "своя"
ZONE_PACKAGE = "вне маркетинга → пакет"
MODEL_SYSTEM = "модель"   # эффект в ₽ принимается только из движка экономики (страж 8)
COUNT_UNITS = ("шт", "доля")   # не денежные единицы
CHANGE_ABSOLUTE = "абсолютное"      # изменение доли в долях: 0,01 = 1 п.п.
CHANGE_RELATIVE = "относительное"   # доля от текущего значения: 0,10 = +10%; в п.п. переводит гейт шума
CHANGE_KINDS = (CHANGE_ABSOLUTE, CHANGE_RELATIVE)
GATE_FIELDS = ("expected_delta", "expected_kind", "noise_share", "noise_threshold")   # объявляет гейт шума
# Доля для порога шума — не старше квартала до старта теста. Решение ревью 15.09.2026, подтверждено Дмитрием 15.09
# («чтобы не городить лишнего шума»); кандидат в канон.
NOISE_SHARE_MAX_AGE_DAYS = 92

# Р1 принято Дмитрием 13.09.2026: владелец и метрика обязательны с перехода в candidate.
# Буквальное чтение стража 6 — IDEA (тогда create() требует всю карточку CANDIDATE_FIELDS).
OWNER_REQUIRED_FROM = HStatus.CANDIDATE

ALLOWED = {
    HStatus.IDEA: {HStatus.RESEARCH, HStatus.ARCHIVE},
    HStatus.RESEARCH: {HStatus.CANDIDATE, HStatus.BLOCKED_NO_DATA, HStatus.ARCHIVE},
    HStatus.BLOCKED_NO_DATA: {HStatus.RESEARCH, HStatus.ARCHIVE},
    HStatus.CANDIDATE: {HStatus.IN_TEST, HStatus.WAITING_OWNER, HStatus.RESEARCH, HStatus.ARCHIVE},
    HStatus.WAITING_OWNER: {HStatus.IN_TEST, HStatus.ARCHIVE},
    HStatus.IN_TEST: {HStatus.MEASURED, HStatus.DEFERRED},
    HStatus.DEFERRED: {HStatus.MEASURED},
    HStatus.MEASURED: {HStatus.CONCLUDED},
    HStatus.CONCLUDED: {HStatus.ARCHIVE},
    HStatus.ARCHIVE: set(),
}

# Поля момента «заведение» по контракту реестра + цикл, главная метрика и владелец (Р1).
CANDIDATE_FIELDS = ("cycle_id", "business_task", "tree_branch", "model_lever", "unit_of_action",
                    "fact_basis", "effect_goal_units", "effect_rub", "mechanic", "main_metric", "owner")
LAUNCH_FIELDS = ("owner", "zone", "change", "start_date", "window_days", "main_metric",
                 "expected_n", "threshold", "failure_criterion", "rat")
CONCLUSION_FIELDS = ("decision", "conclusion", "knowledge_row")


@dataclass(frozen=True)
class Hypothesis:
    id: str
    formulation: str                       # «если — то — потому что»
    status: HStatus = HStatus.IDEA
    version: int = 1
    supersedes: str | None = None
    cycle_id: str = ""
    # заведение
    business_task: str = ""
    tree_branch: str = ""
    model_lever: str = ""
    unit_of_action: UnitOfAction | None = None
    fact_basis: Number | None = None
    effect_goal_units: Number | None = None
    effect_rub: Number | None = None       # только из модели (страж 8)
    mechanic: str = ""
    main_metric: str = ""
    owner: str = ""
    # до запуска
    zone: str = ""
    change: str = ""
    start_date: date | None = None
    window_days: int | None = None
    expected_n: int | None = None
    threshold: float | None = None         # порог ± объявляется до старта (страж 7); в долях: 0,01 = 1 п.п.
    expected_delta: float | None = None    # ожидаемое абсолютное изменение главной доли, в долях (страж 5)
    expected_kind: str = ""                # как объявлено изменение: абсолютное или относительное — ставит гейт
    noise_share: Number | None = None      # главная доля, из которой движок считает порог шума (страж 5)
    noise_threshold: Number | None = None  # порог шума: у гейта — на базе доли, при запуске — на ожидаемом N теста
    failure_criterion: str = ""
    rat: tuple[str, ...] = ()
    deferred_until: date | None = None
    blocked_class: str = ""
    status_reason: str = ""                # почему статус выставлен кодом (например, страж 5)
    # после замера
    measured: Number | None = None
    in_threshold: bool | None = None
    decision: Decision | None = None
    conclusion: str = ""
    knowledge_row: str = ""                # ссылка на строку карты знаний (страж 14)


def _require(h: Hypothesis, guard: int, names) -> None:
    missing = [f for f in names if getattr(h, f) in (None, "", ())]
    if missing:
        raise GuardViolation(guard, f"гипотеза {h.id}: не заполнено {', '.join(missing)}")


def _finite(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _check_noise(h: Hypothesis) -> Hypothesis:
    """Страж 5: порог шума пересчитывается кодом из главной доли гипотезы на ожидаемом N теста — готовому числу порога
    не верим. Объявленный порог и ожидаемое изменение — абсолютные, в долях (0,01 = 1 п.п.), не ниже порога шума."""
    from .economy import noise   # движок экономики берёт из реестра имя системы модели — импорт при вызове

    share = h.noise_share
    if share is None or h.expected_delta is None:
        raise GuardViolation(5, f"гипотеза {h.id}: нет главной доли для порога шума или ожидаемого изменения")
    if h.expected_kind not in CHANGE_KINDS:
        raise GuardViolation(5, f"гипотеза {h.id}: ожидаемое изменение не объявлено гейтом шума с видом изменения")
    if share.metric != h.main_metric:
        raise GuardViolation(5, f"гипотеза {h.id}: порог шума посчитан для «{share.metric}», а главная метрика "
                                f"гипотезы — «{h.main_metric}»")
    if not isinstance(h.start_date, date):
        raise GuardViolation(7, f"гипотеза {h.id}: дата старта {h.start_date!r} — нужна дата")
    if share.as_of is None or share.as_of > h.start_date or share.period_end > h.start_date:
        raise GuardViolation(5, f"гипотеза {h.id}: доля для порога шума снята или закончилась позже старта теста "
                                f"{h.start_date:%d.%m.%Y}")
    if (h.start_date - share.period_end).days > NOISE_SHARE_MAX_AGE_DAYS:
        raise GuardViolation(5, f"гипотеза {h.id}: доля для порога шума закончилась {share.period_end:%d.%m.%Y} — "
                                f"старше {NOISE_SHARE_MAX_AGE_DAYS} дней до старта теста {h.start_date:%d.%m.%Y}")
    for name in ("threshold", "expected_delta"):
        value = getattr(h, name)
        if not _finite(value) or abs(value) > 1:
            raise GuardViolation(5, f"гипотеза {h.id}: {name} = {value!r} — нужна доля: конечное число не больше 1 "
                                    "по модулю (0,01 = 1 п.п.)")
    if h.threshold <= 0:
        raise GuardViolation(5, f"гипотеза {h.id}: порог ± {h.threshold:g} — нужен больше нуля")
    if isinstance(h.expected_n, bool) or not isinstance(h.expected_n, int) or h.expected_n <= 0:
        raise GuardViolation(5, f"гипотеза {h.id}: ожидаемый N теста {h.expected_n!r} — нужно целое больше нуля")
    at_launch = noise(share, base=h.expected_n)
    if at_launch.value is None:
        raise GuardViolation(5, f"гипотеза {h.id}: порог шума при N = {h.expected_n} не определён — {at_launch.missing}")
    if not 0 <= share.value + h.expected_delta <= 1:
        raise GuardViolation(5, f"гипотеза {h.id}: ожидаемая доля {share.value + h.expected_delta:g} вне [0; 1] — "
                                "изменение записано не в долях")
    if h.threshold < at_launch.value:
        raise GuardViolation(5, f"гипотеза {h.id}: объявленный порог {h.threshold:g} ниже шума {at_launch.value:g} "
                                f"при N = {h.expected_n}")
    if abs(h.expected_delta) < at_launch.value:
        raise GuardViolation(5, f"гипотеза {h.id}: ожидаемое изменение {h.expected_delta:g} меньше шума "
                                f"{at_launch.value:g} при N = {h.expected_n} — это research, а не тест")
    return replace(h, noise_threshold=at_launch)


def _check_card(h: Hypothesis) -> None:
    _require(h, 6, CANDIDATE_FIELDS)
    text = f" {h.formulation.lower()} "
    if "если " not in text or " то " not in text or "потому что" not in text:
        raise GuardViolation(6, f"гипотеза {h.id}: формулировка не по формуле «если — то — потому что»")
    if h.fact_basis.status is Status.NO_DATA:
        raise GuardViolation(6, f"гипотеза {h.id}: факт-основание со статусом «нет данных»")
    if h.effect_rub.source_system != MODEL_SYSTEM:
        raise GuardViolation(8, f"гипотеза {h.id}: эффект в ₽ получен не из модели")
    if h.effect_rub.unit in COUNT_UNITS or h.effect_rub.status is Status.NO_DATA:
        raise GuardViolation(8, f"гипотеза {h.id}: эффект в ₽ должен быть денежной величиной со значением")


def create(**fields) -> Hypothesis:
    h = Hypothesis(**fields)
    if h.status is not HStatus.IDEA:
        raise ValueError("гипотеза создаётся в статусе «идея»; дальше — только через transition()")
    if OWNER_REQUIRED_FROM is HStatus.IDEA:
        _check_card(h)
    return h


def transition(h: Hypothesis, to: HStatus, **changes) -> Hypothesis:
    to = HStatus(to)
    if to not in ALLOWED[h.status]:
        raise ValueError(f"переход «{h.status.value} → {to.value}» не предусмотрен контрактом")
    if to in (HStatus.IN_TEST, HStatus.WAITING_OWNER):
        declared = [name for name in GATE_FIELDS if name in changes]
        if declared:
            raise GuardViolation(5, f"гипотеза {h.id}: {', '.join(declared)} объявляются гейтом шума до запуска, "
                                    "а не при запуске")
    new = replace(h, status=to, **changes)
    if to is HStatus.CANDIDATE:
        _check_card(new)
    if to in (HStatus.IN_TEST, HStatus.WAITING_OWNER):
        _require(new, 7, LAUNCH_FIELDS)
        if new.zone not in (ZONE_OWN, ZONE_PACKAGE):
            raise GuardViolation(7, f"гипотеза {h.id}: зона «{new.zone}» не из контракта")
        if to is HStatus.WAITING_OWNER and new.zone != ZONE_PACKAGE:
            raise GuardViolation(7, f"гипотеза {h.id}: «ждёт владельца» — только для пакета вне маркетинга")
        new = _check_noise(new)
    if to is HStatus.BLOCKED_NO_DATA and new.blocked_class not in SOURCE_CLASSES:
        raise GuardViolation(6, f"гипотеза {h.id}: для «blocked: нет данных» нужен класс источника A–F")
    if to is HStatus.DEFERRED:
        _require(new, 7, ("deferred_until",))
    if to is HStatus.MEASURED:
        _require(new, 7, ("measured", "in_threshold"))
    if to is HStatus.CONCLUDED:
        _require(new, 14, CONCLUSION_FIELDS)
        if not isinstance(new.decision, Decision):
            raise GuardViolation(14, f"гипотеза {h.id}: решение «{new.decision}» не из scale / iterate / kill / research")
    return new


def iterate(h: Hypothesis, new_formulation: str, conclusion: str, knowledge_row: str, next_cycle_id: str):
    """Вывод «iterate»: прежняя гипотеза закрывается как есть, новая версия стартует с candidate
    в следующем цикле."""
    closed = transition(h, HStatus.CONCLUDED, decision=Decision.ITERATE,
                        conclusion=conclusion, knowledge_row=knowledge_row)
    base_id = h.id.split(".v")[0]
    successor = Hypothesis(
        id=f"{base_id}.v{h.version + 1}", formulation=new_formulation, status=HStatus.CANDIDATE,
        version=h.version + 1, supersedes=h.id, cycle_id=next_cycle_id, business_task=h.business_task,
        tree_branch=h.tree_branch, model_lever=h.model_lever, unit_of_action=h.unit_of_action,
        fact_basis=h.fact_basis, effect_goal_units=h.effect_goal_units, effect_rub=h.effect_rub,
        mechanic=h.mechanic, main_metric=h.main_metric, owner=h.owner)
    _check_card(successor)
    return closed, successor


LAUNCHED = (HStatus.IN_TEST, HStatus.WAITING_OWNER, HStatus.DEFERRED, HStatus.MEASURED, HStatus.CONCLUDED)


def check_state(h: Hypothesis) -> None:
    """Проверка сохранённой гипотезы для её текущего статуса без перехода (Х10): всё, что требовали переходы до этого
    статуса, обязано быть на месте — карточка (стражи 6, 8), поля запуска и зона (страж 7), порог шума, пересчитанный
    движком (страж 5), замер (страж 7), решение и строка знаний (страж 14). Архив не проверяется: из какого статуса в
    него пришли, по записи не видно."""
    if h.status is HStatus.BLOCKED_NO_DATA and h.blocked_class not in SOURCE_CLASSES:
        raise GuardViolation(6, f"гипотеза {h.id}: для «blocked: нет данных» нужен класс источника A–F")
    if h.status is HStatus.CANDIDATE or h.status in LAUNCHED:
        _check_card(h)
    if h.status in LAUNCHED:
        _require(h, 7, LAUNCH_FIELDS)
        if h.zone not in (ZONE_OWN, ZONE_PACKAGE):
            raise GuardViolation(7, f"гипотеза {h.id}: зона «{h.zone}» не из контракта")
        if h.status is HStatus.WAITING_OWNER and h.zone != ZONE_PACKAGE:
            raise GuardViolation(7, f"гипотеза {h.id}: «ждёт владельца» — только для пакета вне маркетинга")
        if _check_noise(h).noise_threshold != h.noise_threshold:
            raise GuardViolation(5, f"гипотеза {h.id}: порог шума в записи не совпадает с пересчётом движка — запись "
                                    "изменена вне движка")
    if h.status is HStatus.DEFERRED:
        _require(h, 7, ("deferred_until",))
    if h.status in (HStatus.MEASURED, HStatus.CONCLUDED):
        _require(h, 7, ("measured", "in_threshold"))
    if h.status is HStatus.CONCLUDED:
        _require(h, 14, CONCLUSION_FIELDS)
        if not isinstance(h.decision, Decision):
            raise GuardViolation(14, f"гипотеза {h.id}: решение «{h.decision}» не из scale / iterate / kill / research")
