"""Общие построители канонических чисел и гипотез для тестов ядра."""
from datetime import date
from pathlib import Path

from growth_engine.core.config import load_config
from growth_engine.core.number import Number, Status

CFG = load_config(Path(__file__).parent / "fixtures" / "config_minimal.yaml")
MAY = (date(2026, 5, 1), date(2026, 6, 1))
JUN = (date(2026, 6, 1), date(2026, 7, 1))
JUL = (date(2026, 7, 1), date(2026, 8, 1))
AUG = (date(2026, 8, 1), date(2026, 9, 1))
AS_OF = date(2026, 9, 3)    # дата съёма замороженных снимков
LATER = date(2026, 9, 14)   # более поздний съём тех же окон


def n(metric, value, scope="p1", period=AUG, flow="all", status=Status.FACT,
      source="C:analytics-x:data", as_of=AS_OF):
    return Number(metric=metric, level=CFG.rule(metric).level, scope=scope, flow=flow,
                  period_start=period[0], period_end=period[1], source=source,
                  status=status, value=value, as_of=as_of)


# Импорт после n(): registry зависит от ladders и number, но не от helpers.
from growth_engine.core.ladders import UnitOfAction  # noqa: E402
from growth_engine.core.registry import CHANGE_ABSOLUTE, ZONE_OWN, Decision, HStatus, create, transition  # noqa: E402
from growth_engine.core.arithmetic import ratio  # noqa: E402
from growth_engine.core.cycle import apply_noise_gate  # noqa: E402

FORMULA = ("если убрать шаг 2 формы подбора, то CR1 страницы вырастет, "
           "потому что на шаге 2 теряется половина начавших")
UNIT = UnitOfAction("A", {"url": "/catalog/item/", "element": "форма подбора, шаг 2"})
FACT = n("leads", 1051, flow="web")
EFFECT = n("sales_entry", 12, status=Status.ESTIMATE)
EFFECT_RUB = Number(metric="gross_profit", level="money", scope="p1", flow="all",
                    period_start=AUG[0], period_end=AUG[1], source="D:модель:эффект гипотезы",
                    status=Status.ESTIMATE, value=180000.0, unit="₽", as_of=AS_OF)
CARD = dict(cycle_id="Ц-1", business_task="объём лидов", tree_branch="B1", model_lever="CR1 веб-потока",
            unit_of_action=UNIT, fact_basis=FACT, effect_goal_units=EFFECT, effect_rub=EFFECT_RUB,
            mechanic="вычитание шага", main_metric="cr1", owner="Дмитрий")
# Главная доля гипотезы — веб-CR1 окна августа (1 578 / 101 146; окно не старше квартала до старта 20.09). Гейт шума
# объявляет ожидание +0,40 п.п.; при запуске реестр считает порог шума на ожидаемом N теста 9 000:
# 2·√(p(1−p)/9000) ≈ 0,26 п.п., поэтому объявленный порог — 0,30 п.п.
NOISE_SHARE = ratio(n("leads", 1578, flow="web"), n("visits", 101146, flow="web"), "cr1", CFG)
LAUNCH = dict(zone=ZONE_OWN, change="убрать шаг 2 формы подбора", start_date=date(2026, 9, 20),
              window_days=28, expected_n=9000, threshold=0.003,
              failure_criterion="CR1 страницы не вышел за порог за окно",
              rat=("посетители бросают форму на шаге 2",))


def candidate(hid="H-001", **card):
    h = transition(create(id=hid, formulation=FORMULA), HStatus.RESEARCH)
    return transition(h, HStatus.CANDIDATE, **{**CARD, **card})


def gated(hid="H-001", change=0.004, **card):
    """Кандидат после гейта шума: ожидание объявлено абсолютным изменением главной доли."""
    return apply_noise_gate(candidate(hid, **card), NOISE_SHARE, change, CHANGE_ABSOLUTE)


# Замер теста — главная доля (CR1 веб) за окно теста 20.09–18.10.2026, снятая 19.10: 1 640 / 101 146 — изменение
# +0,06 п.п. от доли гейта, ниже объявленного порога 0,30 п.п. Прежний замер «заявки 1 100 за август» совпадал по
# идентичности с фактом-основанием при другом значении — это поймал страж 13 хранилища (задача 5.1).
TEST_WINDOW = (date(2026, 9, 20), date(2026, 10, 18))
MEASURED_ON = date(2026, 10, 19)


def measured(hid="H-001"):
    h = transition(gated(hid), HStatus.IN_TEST, **LAUNCH)
    share = ratio(n("leads", 1640, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON),
                  n("visits", 101146, flow="web", period=TEST_WINDOW, as_of=MEASURED_ON), "cr1", CFG)
    return transition(h, HStatus.MEASURED, measured=share, in_threshold=False)


def concluded(hid="H-001", knowledge_row="K-001"):
    return transition(measured(hid), HStatus.CONCLUDED, decision=Decision.KILL,
                      conclusion="эффект ниже порога", knowledge_row=knowledge_row)
