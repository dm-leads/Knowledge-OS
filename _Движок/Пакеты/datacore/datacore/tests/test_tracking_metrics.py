"""Метрики этапа 3 из фактов: визиты без роботов (сложение кабинетов — К7), звонки со связанным визитом, New First SQL
по своей группе каналов — маркер из кабинета бренда сделки."""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from datacore.schema.config import load_config
from datacore.schema.errors import RuleViolation
from datacore.schema.number import Status
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import REGISTRY, calls, new_first_sql, new_first_sql_by_channel, visits
from datacore.tests.helpers import SYNTHETIC
from datacore.tests.test_metrics_ask import seed

CFG = load_config(SYNTHETIC)
LOADED = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
AUG = (date(2026, 8, 1), date(2026, 9, 1))


def tracked(engine, scope, visits_rows=(), call_rows=(), order_rows=(), covered=(date(2026, 8, 1), date(2026, 9, 30))):
    """covered — период, который загрузка считает покрытым: метрики визитов и звонков без журнала окон честно отвечают
    «нет данных» (ревью Codex этапа 3), поэтому фикстура закрывает окно явно."""
    system = CFG.tracking["system_by_scope"][scope]
    p = Provenance(system, "C", f"L-{scope}-{uuid4().hex[:8]}", LOADED)     # у каждой загрузки свой id
    register_load(engine, p, date(2026, 9, 14))
    for table, rows in (("visit", visits_rows), ("phone_call", call_rows), ("order_marker", order_rows)):
        if rows:
            write_facts(engine, table, list(rows), p, mode="upsert")
    if covered:
        lo = datetime(covered[0].year, covered[0].month, covered[0].day, tzinfo=timezone.utc)
        hi = datetime(covered[1].year, covered[1].month, covered[1].day, tzinfo=timezone.utc)
        engine.execute("INSERT INTO meta.window_log (source_system, window_start, window_end, rows_loaded, min_started_at, "
                       "max_started_at, load_id, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT (source_system, window_start) DO UPDATE SET max_started_at = EXCLUDED.max_started_at",
                       (system, 0, 1000000, len(visits_rows), lo, hi, p.load_id, LOADED))
        engine.commit()
    finish_load(engine, p.load_id, rows_read=1, rows_written=1, status="ok")


def v(i, day, group):
    return dict(visit_id=i, started_at=datetime(2026, 9, day, 9, tzinfo=timezone.utc), marker=group, marker_level_1=group,
                landing_page="/", referrer_host=None, client_hash=None, device=None, geo=None)


def test_visits_exclude_bot_group_and_refuse_company_sum(engine):
    seed(engine)
    tracked(engine, "b1", [v(1, 1, "seo"), v(2, 1, "bot"), v(3, 2, ""), v(4, 10, "seo")])
    n = visits(engine, CFG, "b1", date(2026, 9, 1), date(2026, 9, 10))
    assert (n.value, n.status, n.as_of) == (2, Status.FACT, date(2026, 9, 14))          # бот не в счёте, прямой — в счёте
    with pytest.raises(RuleViolation) as e:
        visits(engine, CFG, "company", date(2026, 9, 1), date(2026, 9, 10))
    assert e.value.code == "К7"


def test_calls_all_and_with_visit(engine):
    seed(engine)
    at = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    rows = [dict(call_id=1, started_at=at, visit_id=10, scenario=None, callee_line=None, caller_hash=None, answered=True, duration_s=5, deal_id=None),
            dict(call_id=2, started_at=at, visit_id=None, scenario=None, callee_line=None, caller_hash=None, answered=False, duration_s=0, deal_id=None)]
    # Звонок 1 ссылается на визит 10 — по правилу «звонок с визитом» этот визит обязан быть в фактах того же кабинета
    # (ревью Codex этапа 3): раньше засчитывался любой непустой номер визита.
    tracked(engine, "b2", visits_rows=[v(10, 1, "seo")], call_rows=rows)
    assert calls(engine, CFG, "b2", date(2026, 9, 1), date(2026, 9, 9)).value == 2
    assert calls(engine, CFG, "b2", date(2026, 9, 1), date(2026, 9, 9), segment="visit=linked").value == 1


def test_new_first_sql_by_channel_uses_cabinet_of_deal_brand(engine):
    seed(engine)
    deals = engine.fetchall("SELECT deal_id, brand, created_at FROM facts.deal WHERE is_new_first AND deleted_at IS NULL "
                            "AND created_at >= TIMESTAMPTZ '2026-08-01 00:00:00+03' AND created_at < TIMESTAMPTZ '2026-09-01 00:00:00+03'")
    assert deals, "seed должен давать New First в августе"
    for scope in ("b1", "b2"):
        mine = [d for d in deals if d[1] == scope]
        other = "b2" if scope == "b1" else "b1"
        tracked(engine, scope, order_rows=[dict(deal_id=d, marker="nosource-crm", visit_id=None, ordered_at=c, marker_level_1="nosource-crm") for d, _, c in mine])
        tracked(engine, other, order_rows=[dict(deal_id=d, marker="pik", visit_id=None, ordered_at=c, marker_level_1="pik") for d, _, c in mine])
    total = new_first_sql(engine, CFG, "b1", *AUG).value     # те же правила: без удалённых и исключённых
    n = new_first_sql_by_channel(engine, CFG, "b1", *AUG, segment="channel_group=nosource-crm")
    assert (n.value, n.segment) == (total, "channel_group=nosource-crm")
    assert new_first_sql_by_channel(engine, CFG, "b1", *AUG, segment="channel_group=pik").value == 0     # маркер чужого кабинета не считается
    with pytest.raises(RuleViolation) as e:
        new_first_sql_by_channel(engine, CFG, "b1", *AUG)
    assert e.value.code == "К2"
    assert set(REGISTRY) >= {"new_first_sql", "visits", "calls", "new_first_sql_by_channel"}
