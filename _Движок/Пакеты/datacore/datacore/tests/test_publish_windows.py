"""Публикация 1.5.0: окна короче месяца (дни, недели пн–вс, текущий месяц), статус незакрытого и недозревшего окна,
разрез только у поддерживающих метрик; звонки трекера после даты отключения — «нет данных»."""
from datetime import date, datetime, timedelta, timezone

import yaml

from datacore.schema.config import load_config
from datacore.schema.number import Status
from datacore.schema.writer import write_facts
from datacore.serve.metrics import calls
from datacore.serve.publish import (collect, days_back, mark_windows, month_to_date, periods_for, weeks_back)
from datacore.tests.helpers import SYNTHETIC
from datacore.tests.test_metrics_ask import MSK, PROV, seed
from datacore.tests.test_tracking_metrics import tracked

CFG = load_config(SYNTHETIC)


def test_window_shapes():
    fri = date(2026, 11, 13)
    assert days_back(fri, 3) == [(date(2026, 11, 10), date(2026, 11, 11)), (date(2026, 11, 11), date(2026, 11, 12)),
                                 (date(2026, 11, 12), date(2026, 11, 13))]                 # сегодня не закрыт — не входит
    assert weeks_back(fri, 2) == [(date(2026, 10, 26), date(2026, 11, 2)), (date(2026, 11, 2), date(2026, 11, 9))]
    assert weeks_back(date(2026, 11, 16), 1) == [(date(2026, 11, 9), date(2026, 11, 16))]  # в понедельник прошлая закрыта
    assert month_to_date(fri) == [(date(2026, 11, 1), fri)] and month_to_date(date(2026, 11, 1)) == []
    p = periods_for(fri, months=1, weeks=1, days=2, mtd=True)
    assert len(p) == len(set(p)) == 5 and p[0] == (date(2026, 10, 1), date(2026, 11, 1))


def test_closed_week_equals_sum_of_its_days(engine):
    """Тест из постановки Движка: за закрытую неделю сумма дней совпадает с недельным числом — по каждому кабинету,
    включая сделку на границе суток по поясу инстанса."""
    seed(engine)
    monday = date(2026, 8, 10)
    assert monday.weekday() == 0
    extra = [dict(deal_id=20 + i, contact_id=1 + i % 3, brand=("b1", "b2")[i % 2], pipeline="p", status="s",
                  is_new_first=True, created_at=at)
             for i, at in enumerate([datetime(2026, 8, 10, 0, 5, tzinfo=MSK), datetime(2026, 8, 13, 12, tzinfo=MSK),
                                     datetime(2026, 8, 16, 23, 55, tzinfo=MSK), datetime(2026, 8, 17, 0, 1, tzinfo=MSK),
                                     datetime(2026, 8, 9, 23, 59, tzinfo=MSK)])]
    write_facts(engine, "deal", extra, PROV)
    after = monday + timedelta(days=7)
    rows = collect(engine, CFG, days_back(after, 7) + weeks_back(after, 1), ("new_first_sql",), ["b1", "b2", "company"])
    for scope in ("b1", "b2", "company"):
        mine = [r for r in rows if r["scope"] == scope]
        week = [r for r in mine if r["period_start"] == monday.isoformat() and r["period_end"] == after.isoformat()]
        days = [r for r in mine if r not in week]
        assert len(week) == 1 and len(days) == 7
        assert sum(r["value"] for r in days) == week[0]["value"], scope
    company_week = next(r for r in rows if r["scope"] == "company" and r["period_start"] == monday.isoformat()
                        and r["period_end"] == after.isoformat())
    assert company_week["value"] == 4         # 10.08 00:05, 10.08 (сделка 3), 13.08, 16.08 23:55; не 09.08 и не 17.08


def _row(metric, start, end, status="факт", value=1.0, missing=""):
    return dict(metric=metric, period_start=start.isoformat(), period_end=end.isoformat(), status=status, value=value,
                missing=missing)


def test_unclosed_month_and_young_deal_windows_are_estimates():
    today = date(2026, 11, 13)
    rows = [_row("payments", date(2026, 11, 1), today),                       # месяц не закрыт
            _row("mql", date(2026, 11, 10), date(2026, 11, 11)),               # моложе 7 дней
            _row("mql", date(2026, 11, 2), date(2026, 11, 3)),                 # дозрел
            _row("revenue", date(2026, 11, 12), date(2026, 11, 13)),           # деньги дозревают с загрузкой
            _row("new_first_sql", date(2026, 11, 12), date(2026, 11, 13), status="оценка", missing="загрузка не завершена"),
            _row("visits", date(2026, 11, 12), date(2026, 11, 13), status="нет данных", value=None)]
    mark_windows(rows, today, 7, [(date(2026, 11, 1), today)])
    assert rows[0]["status"] == "оценка" and "месяц не закрыт: окно по 12.11.2026" in rows[0]["missing"]
    assert rows[1]["status"] == "оценка" and "моложе 7 дн." in rows[1]["missing"]
    assert rows[2]["status"] == "факт" and rows[3]["status"] == "факт"
    assert rows[4]["missing"].startswith("загрузка не завершена; окно моложе")  # прежняя причина сохраняется
    assert rows[5]["status"] == "нет данных" and not rows[5]["missing"]          # «нет данных» не трогается


def test_segment_goes_only_to_metrics_that_support_it(engine):
    seed(engine)
    tracked(engine, "b1")
    rows = collect(engine, CFG, [(date(2026, 8, 1), date(2026, 9, 1))], ("visits", "new_first_sql"), ["b1"],
                   ["", "contour=digital"])
    assert not [r for r in rows if r["metric"] == "visits" and r["segment"]]
    assert [r for r in rows if r["metric"] == "new_first_sql" and r["segment"] == "contour=digital"]


def test_calls_after_tracker_switch_off_are_no_data(engine, tmp_path):
    raw = yaml.safe_load(SYNTHETIC.read_text(encoding="utf-8"))
    raw["tracking"]["calls_off_since"] = {"b1": "2026-09-05"}
    raw["tracking"]["calls_off_reason"] = "API звонков не оплачено (option_not_paid)"
    path = tmp_path / "instance.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    cfg = load_config(path)
    seed(engine)
    at = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    tracked(engine, "b1", call_rows=[dict(call_id=1, started_at=at, visit_id=None, scenario=None, callee_line=None,
                                          caller_hash=None, answered=True, duration_s=5, deal_id=None)])
    before = calls(engine, cfg, "b1", date(2026, 9, 1), date(2026, 9, 5))
    after = calls(engine, cfg, "b1", date(2026, 9, 1), date(2026, 9, 10))
    assert (before.value, before.status) == (1, Status.FACT)
    assert after.status == Status.NO_DATA and after.value is None and "05.09.2026 — API звонков не оплачено" in after.missing


def test_closed_week_from_first_monday_is_not_an_open_month(tmp_path):
    """Ревью 13.11.2026: 01.02.2027 — понедельник; 08.02 закрытая неделя 01–08.02 совпадает с месяцем нарастающим.
    Она остаётся фактом: «месяц не закрыт» ставится только окну, заказанному как месяц нарастающим."""
    from datacore.serve.publish import months_back
    today = date(2027, 2, 8)
    closed = set(months_back(today, 0) + weeks_back(today, 1) + days_back(today, 0))
    open_windows = [w for w in month_to_date(today) if w not in closed]
    assert open_windows == []
    rows = [_row("payments", date(2027, 2, 1), today)]
    mark_windows(rows, today, 7, open_windows)
    assert rows[0]["status"] == "факт"


def test_empty_publication_keeps_yesterdays_file(tmp_path, monkeypatch):
    """Ревью 13.11.2026: ноль чисел — прошлый файл не заменяется пустым списком."""
    import json
    from datacore.serve import publish as pub
    out = tmp_path / "growth.json"
    out.write_text(json.dumps({"numbers": [{"metric": "x"}]}), encoding="utf-8")
    monkeypatch.setattr(pub, "connect", lambda *a, **k: _Null())
    monkeypatch.setattr(pub, "collect", lambda *a, **k: [])
    monkeypatch.setattr(pub, "last_as_of", lambda *a, **k: None)
    report = pub.publish(CFG, "duckdb:///:memory:", out, months=1, today=date(2026, 11, 13))
    assert report["чисел"] == 0 and "не заменён" in report["файл"]
    assert json.loads(out.read_text(encoding="utf-8"))["numbers"] == [{"metric": "x"}]
    assert not (tmp_path / "growth.json.tmp").exists()


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
