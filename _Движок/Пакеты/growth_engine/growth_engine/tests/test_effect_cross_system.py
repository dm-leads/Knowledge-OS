"""1.9.3: эффект через вторую систему — доля из веб-аналитики, ступени до цели и деньги из системы денежной модели."""
from dataclasses import replace
from datetime import date

import pytest

from growth_engine.core.arithmetic import ratio
from growth_engine.core.errors import GuardViolation
from growth_engine.core.number import Number, Status
from growth_engine.core.storage import number_id
from growth_engine.effect import compute
from growth_engine.tests.helpers import AUG, CFG, n

TAKE = date(2026, 9, 3)
SHARE = replace(ratio(n("leads", 113, flow="web", period=(date(2026, 8, 3), date(2026, 8, 31)), source="A:metrika:data"),
                      n("visits", 3700, flow="web", period=(date(2026, 8, 3), date(2026, 8, 31)), source="A:metrika:data"),
                      "cr_site", CFG), segment="landing_page=/")
LEADS = n("leads", 1000, flow="web", source="D:datacore:publish", as_of=TAKE)
GOAL = n("sales_entry", 380, flow="web", source="D:datacore:publish", as_of=TAKE)
AMPU = Number(metric="ampu", level="money", scope="p1", flow="all", period_start=AUG[0], period_end=AUG[1],
              source="D:datacore:publish", status=Status.ESTIMATE, value=15000.0, unit="₽ на sales_entry", as_of=TAKE)
FIELDS = {"hypothesis": "H-1", "share_id": number_id(SHARE), "change": 0.01, "kind": "абсолютное",
          "scope": "p1", "flow": "web", "month": "2026-08"}


def test_effect_crosses_systems_as_an_estimate_with_a_named_caveat():
    got = compute([SHARE, LEADS, GOAL, AMPU], FIELDS, CFG, "datacore", TAKE)
    per_month = 3700 * 30 / 28
    assert got["effect_goal_units"].value == pytest.approx(per_month * 0.01 * 380 / 1000)
    assert got["effect_goal_units"].status is Status.ESTIMATE and "две системы" in got["effect_goal_units"].missing
    assert got["fact_basis"] is SHARE and got["share"] is None
    assert got["effect_rub"].value == pytest.approx(got["effect_goal_units"].value * 15000)


def test_money_of_another_take_is_refused():
    with pytest.raises(GuardViolation, match="разные съёмы"):
        compute([SHARE, LEADS, GOAL, replace(AMPU, as_of=date(2026, 9, 10))], FIELDS, CFG, "datacore", TAKE)


def test_share_of_another_scope_flow_or_period_is_refused():
    with pytest.raises(GuardViolation, match="смешанная"):
        compute([SHARE, LEADS, GOAL, AMPU], {**FIELDS, "flow": "all"}, CFG, "datacore", TAKE)
    with pytest.raises(GuardViolation, match="не пересекается"):
        compute([SHARE, LEADS, GOAL, AMPU], {**FIELDS, "month": "2026-10"}, CFG, "datacore", TAKE)
