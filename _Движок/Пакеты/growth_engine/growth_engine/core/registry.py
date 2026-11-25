"""Контракт реестра гипотез: поля по трём моментам, статусы и переходы (стражи 5, 6, 7, 8, 14)."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
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
TEST_AB = "ab"                             # схема A/B: два плеча одного окна (1.9.3)
TEST_DESIGNS = ("", TEST_AB)
GATE_FIELDS = ("expected_delta", "expected_kind", "noise_share", "noise_threshold", "test_design", "ab_variant",
               "ab_control")   # объявляет гейт шума
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
    # «в тесте → архив» — только тест, который не стартовал (1.7.6): без замера и с причиной словами, иначе страж 7.
    HStatus.IN_TEST: {HStatus.MEASURED, HStatus.DEFERRED, HStatus.ARCHIVE},
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
# С 1.9.0 карточка запуска несёт ещё защитную метрику с порогом ухудшения и критерии остановки до конца окна (стандарт
# гипотез, раздел «Карточка гипотезы», 17.11.2026); флаг «нет исходного замера» запрещает запуск. Запуски до 1.9.0 этих
# полей не имеют и задним числом по ним не проверяются (`is_legacy_card`).
CARD_1_9_FIELDS = ("guard_metric", "guard_threshold", "stop_criteria")
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
    test_design: str = ""                  # схема теста (1.9.3): "" — база до старта против окна, "ab" — два плеча окна
    ab_variant: str = ""                   # A/B: разрез варианта, объявленный гейтом («ab_h17=B»)
    ab_control: str = ""                   # A/B: разрез контроля, объявленный гейтом («ab_h17=A»)
    noise_share: Number | None = None      # главная доля, из которой движок считает порог шума (страж 5)
    noise_threshold: Number | None = None  # порог шума: у гейта — на базе доли, при запуске — на ожидаемом N теста
    failure_criterion: str = ""
    rat: tuple[str, ...] = ()
    guard_metric: str = ""                 # защитная метрика: что не должно ухудшиться (страж 7, с 1.9.0)
    guard_threshold: float | None = None   # порог её ухудшения, доля больше нуля: 0,10 = на 10 %
    stop_criteria: tuple[str, ...] = ()    # когда прекращаем раньше конца окна — не то же, что критерий провала
    baseline_missing: bool = False         # нет исходного значения метрики: «требует замера до запуска»
    deferred_until: date | None = None
    blocked_class: str = ""
    status_reason: str = ""                # почему статус выставлен кодом (например, страж 5)
    # после замера
    measured: Number | None = None
    control: Number | None = None          # A/B: доля контрольного плеча того же окна и съёма (1.9.3)
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


def _check_noise(h: Hypothesis, legacy: bool = False) -> Hypothesis:
    """Страж 5: порог шума пересчитывается кодом из главной доли гипотезы на ожидаемом N теста — готовому числу порога
    не верим. Объявленный порог и ожидаемое изменение — абсолютные, в долях (0,01 = 1 п.п.), не ниже порога шума.

    С 1.8.0 порог — шум разницы двух долей (база до старта и окно теста), а ожидаемое изменение — не меньше минимума
    мощности 80 % (2,842·SE_D). legacy=True — проверка записи, запущенной до 1.8.0 (порог одной доли «ожидаемый N
    теста=N»): объявленное до старта не переписывается (страж 7), поэтому такая запись сверяется по правилу своего
    запуска. Новые запуски — всегда по новому правилу."""
    from .economy import NOISE_POWER_Z, NOISE_VERDICT_Z, noise, noise_ab, noise_diff, noise_min_effect   # экономика импортирует реестр — импорт при вызове

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
    if h.test_design == TEST_AB:
        at_launch = noise_ab(share, h.expected_n, h.expected_n)   # ожидаемый N — визиты одного плеча (1.9.3)
    else:
        at_launch = noise(share, base=h.expected_n) if legacy else noise_diff(share, h.expected_n)
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
    # Мощность 80 % — относительно фактического порога вердикта: объявленный выше шума порог поднимает минимум.
    minimum = max(noise_min_effect(at_launch.value),
                  h.threshold + NOISE_POWER_Z * at_launch.value / NOISE_VERDICT_Z)
    if not legacy and abs(h.expected_delta) < minimum:
        raise GuardViolation(5, f"гипотеза {h.id}: тест не увидит такой эффект при этом объёме (мощность < 80 %) — "
                                f"ожидаемое изменение {h.expected_delta:g} меньше минимума {minimum:g} при пороге "
                                f"вердикта {max(h.threshold, at_launch.value):g} (шум {at_launch.value:g}) и "
                                f"N = {h.expected_n} — это research, а не тест")
    return replace(h, noise_threshold=at_launch)


def is_legacy_card(h: Hypothesis) -> bool:
    """Запуск до 1.9.0: у запущенной записи нет ни одного поля карточки 1.9.0 и не поднят флаг «нет исходного замера».

    Запуск 1.9.0 пишет защитную метрику, её порог и критерии остановки вместе — страж 7 не пускает в тест без любого из
    них. Поэтому пустые все три — запись прежней версии; заполнена хотя бы часть — запись новой, и она проверяется
    целиком (стёртое поле — правка вне движка)."""
    return (h.status in LAUNCHED and not h.baseline_missing
            and all(getattr(h, name) in (None, "", ()) for name in CARD_1_9_FIELDS))


def _card_value(value):
    """Значение поля карточки для сравнения: список и кортеж критериев остановки — одно и то же."""
    return tuple(value) if isinstance(value, list) else value


def _check_baseline(h: Hypothesis) -> None:
    if h.baseline_missing:
        raise GuardViolation(7, f"гипотеза {h.id}: нет исходного замера метрики — требует замера до запуска: без "
                                "исходного значения порог шума и эффект не посчитать, значение не берётся «примерно»")


def _check_launch_card(h: Hypothesis) -> None:
    """Страж 7 (с 1.9.0): до старта объявлены не только порог главной метрики, но и защитная метрика с порогом
    ухудшения и критерии остановки; без исходного замера тест не запускается."""
    _check_baseline(h)
    if not h.guard_metric.strip() or h.guard_threshold is None:
        raise GuardViolation(7, f"гипотеза {h.id}: не объявлена защитная метрика с порогом ухудшения (guard_metric, "
                                "guard_threshold) — без неё вредный побочный эффект теста не остановить")
    if h.guard_metric.strip() == h.main_metric.strip():
        raise GuardViolation(7, f"гипотеза {h.id}: защитная метрика «{h.guard_metric}» совпадает с главной — нужна "
                                "другая метрика, которая не должна ухудшиться")
    if not _finite(h.guard_threshold) or not 0 < h.guard_threshold <= 1:
        raise GuardViolation(7, f"гипотеза {h.id}: порог защитной метрики {h.guard_threshold!r} — нужна доля больше "
                                "нуля и не больше 1 (0,10 = ухудшение на 10 %)")
    criteria = h.stop_criteria if isinstance(h.stop_criteria, tuple) else ()
    if not criteria or any(not isinstance(c, str) or not c.strip() for c in criteria):
        raise GuardViolation(7, f"гипотеза {h.id}: не заданы критерии остановки до конца окна (stop_criteria) — "
                                "нужен хотя бы один, словами")
    failure = h.failure_criterion.strip().casefold()
    if any(c.strip().casefold() == failure for c in criteria):
        raise GuardViolation(7, f"гипотеза {h.id}: критерий остановки повторяет критерий провала — нужны оба, не "
                                "одно вместо другого")


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
    if h.status in LAUNCHED and to not in (HStatus.IN_TEST, HStatus.WAITING_OWNER):
        # Поля карточки 1.9.0 объявляются при запуске и проверяются стражем 7 только там. Переписать или стереть их
        # другим переходом нельзя: запись со стёртыми полями выглядела бы запуском до 1.9.0 (ревью Codex 22.11.2026).
        rewritten = [name for name in CARD_1_9_FIELDS + ("baseline_missing",)
                     if name in changes and _card_value(changes[name]) != _card_value(getattr(h, name))]
        if rewritten:
            raise GuardViolation(7, f"гипотеза {h.id}: {', '.join(rewritten)} объявлены при запуске — после запуска "
                                    "не переписываются")
        changes = {k: v for k, v in changes.items() if k not in CARD_1_9_FIELDS + ("baseline_missing",)}
    if h.status is HStatus.IN_TEST and to is HStatus.ARCHIVE:
        # Запуск, которого не было на деле (вариант не выложили), закрывается архивом с причиной. Тест, у которого есть
        # замер, так закрыть нельзя: его исход решает замер и вывод цикла, а не архив (1.7.6).
        if h.measured is not None:
            raise GuardViolation(7, f"гипотеза {h.id}: у теста есть замер — закрывается выводом цикла, а не архивом")
        if not str(changes.get("status_reason") or "").strip() or changes.get("not_started") is not True:
            raise GuardViolation(7, f"гипотеза {h.id}: «в тесте → архив» — только для теста, который не стартовал: "
                                    "нужны признак not_started: true и причина словами (status_reason)")
        # После конца окна архив запрещён: тест с прошедшим окном закрывает замер или откладывание, иначе архивом
        # прятали бы неудачный тест без замера (ревью 1.8.1). Проверка кодом, а не текстом причины.
        if h.start_date and h.window_days and date.today() >= h.start_date + timedelta(days=int(h.window_days)):
            raise GuardViolation(7, f"гипотеза {h.id}: окно теста кончилось — закрывается замером или отложенным "
                                    "замером, а не архивом")
        changes = {k: v for k, v in changes.items() if k != "not_started"}
        changes["status_reason"] = "тест не стартовал: " + str(changes["status_reason"]).strip()
    new = replace(h, status=to, **changes)
    if to is HStatus.CANDIDATE:
        _check_card(new)
    if to in (HStatus.IN_TEST, HStatus.WAITING_OWNER):
        _check_baseline(new)               # причина — нет замера, а не пустые поля, которые из-за этого пусты
        _require(new, 7, LAUNCH_FIELDS)
        _check_launch_card(new)
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
        if not is_legacy_card(h):
            _check_launch_card(h)          # запуски до 1.9.0 задним числом не проверяются — как порог шума в 1.8.0
        if h.zone not in (ZONE_OWN, ZONE_PACKAGE):
            raise GuardViolation(7, f"гипотеза {h.id}: зона «{h.zone}» не из контракта")
        if h.status is HStatus.WAITING_OWNER and h.zone != ZONE_PACKAGE:
            raise GuardViolation(7, f"гипотеза {h.id}: «ждёт владельца» — только для пакета вне маркетинга")
        from .economy import is_legacy_noise   # экономика импортирует реестр — импорт при вызове
        if _check_noise(h, legacy=is_legacy_noise(h.noise_threshold)).noise_threshold != h.noise_threshold:
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
