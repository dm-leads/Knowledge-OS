"""1.6.0, задача Движка роста 17.11.2026: поток web / no_visit у ступеней воронки и метрика «заявки».

web — у сделки есть маркер заказа из кабинета её бренда, у маркера есть визит, и визит лежит в фактах того же кабинета;
no_visit — остальные сделки той же выборки. Тест из постановки: за закрытый месяц web + no_visit = all по каждому
кабинету."""
from datetime import date, datetime, timezone

import pytest

from datacore.schema.config import load_config
from datacore.schema.errors import RuleViolation
from datacore.schema.number import Status
from datacore.serve.metrics import leads, mql, new_first_sql, paid_deals_crm
from datacore.serve.publish import FLOW_SUPPORT, collect
from datacore.tests.helpers import SYNTHETIC
from datacore.tests.test_mql import load
from datacore.tests.test_tracking_metrics import tracked, v

CFG = load_config(SYNTHETIC)
SEP = (date(2026, 9, 1), date(2026, 10, 1))


def d(deal_id, *, brand="b1", pipeline="300", status="142", route="Маршрут А", new_first=True, day=5, closed_day=None):
    return dict(deal_id=deal_id, contact_id=1, brand=brand, pipeline=pipeline, status=status,
                created_at=datetime(2026, 9, day, 10, tzinfo=timezone.utc), is_new_first=new_first,
                qualification_route=route, qualification_route_legacy=None, close_reason=None,
                closed_at=datetime(2026, 9, closed_day, 10, tzinfo=timezone.utc) if closed_day else None,
                deleted_at=None)


def marker(deal_id, visit_id):
    return dict(deal_id=deal_id, marker="seo", visit_id=visit_id, ordered_at=datetime(2026, 9, 5, 9, tzinfo=timezone.utc),
                marker_level_1="seo")


def world(engine):
    """b1: 1 — визит в фактах (web); 2 — визит в маркере, но не в фактах; 3 — маркер без визита; 4 — без маркера;
    5 — визит в фактах ЧУЖОГО кабинета (маркер чужого кабинета не считается). b2: 11 — web, 12 — без маркера.
    Сделки 1–5, 11–12 — воронка квалификации 300 (MQL, заявки, New First); 21–22 — выигранные продажи воронки 100;
    31 — заявка дилерской воронки 400 (не MQL); 41 — удалена; неопределённый бренд — 51."""
    load(engine, [d(1), d(2), d(3), d(4), d(5), d(11, brand="b2"), d(12, brand="b2"),
                  d(21, pipeline="100", closed_day=10, new_first=False), d(22, pipeline="100", closed_day=12, new_first=False),
                  d(31, pipeline="400", new_first=False, route=None),
                  {**d(41), "deleted_at": datetime(2026, 9, 6, tzinfo=timezone.utc)},
                  d(51, brand="unknown", new_first=False)])
    tracked(engine, "b1", visits_rows=[v(101, 5, "seo"), v(121, 5, "seo"), v(131, 5, "seo")],
            order_rows=[marker(1, 101), marker(2, 999), marker(3, None), marker(21, 121), marker(22, None),
                        marker(31, 131)])
    tracked(engine, "b2", visits_rows=[v(201, 5, "seo"), v(205, 5, "seo")],
            order_rows=[marker(11, 201), marker(5, 205)])


@pytest.mark.parametrize("fn", [new_first_sql, mql, paid_deals_crm, leads])
def test_web_plus_no_visit_equals_all_for_every_cabinet(engine, fn):
    world(engine)
    for scope in ("b1", "b2", "company"):
        if fn is paid_deals_crm and scope == "company":
            continue                                    # сумма кабинетов: проверяется отдельно, у неё своя подпись
        a, w, n = (fn(engine, CFG, scope, *SEP, flow=f) for f in ("all", "web", "no_visit"))
        assert (a.flow, w.flow, n.flow) == ("all", "web", "no_visit")
        assert w.value + n.value == a.value, (fn.__name__, scope, a.value, w.value, n.value)


def test_web_needs_a_visit_in_facts_of_the_own_cabinet(engine):
    world(engine)
    w = new_first_sql(engine, CFG, "b1", *SEP, flow="web")
    n = new_first_sql(engine, CFG, "b1", *SEP, flow="no_visit")
    assert (w.value, n.value) == (1, 4)              # web — только сделка 1: у 2 визита нет в фактах, у 5 — чужой кабинет
    assert w.status is Status.ESTIMATE and "без маркера заказа трекера: 2" in w.missing    # сделки 4 и 5
    assert new_first_sql(engine, CFG, "b1", *SEP).status is Status.FACT                    # «all» — как раньше
    assert (paid_deals_crm(engine, CFG, "b1", *SEP, flow="web").value,
            paid_deals_crm(engine, CFG, "b1", *SEP, flow="no_visit").value) == (1, 1)


def test_leads_are_all_deals_of_intake_pipelines(engine):
    world(engine)
    b1 = leads(engine, CFG, "b1", *SEP)
    assert b1.value == 6 and b1.metric == "leads" and b1.level == "lead"  # 1–5 и дилерская 31; без 21–22, удалённой 41
    assert "неопределённым брендом в окне: 1" in b1.missing and b1.status is Status.ESTIMATE
    company = leads(engine, CFG, "company", *SEP)
    assert company.value == 9                                                # + b2 11, 12 и неопределённый 51
    assert "неопределённым брендом в окне: 1" in company.missing and company.status is Status.ESTIMATE   # ревью 1.6.0
    assert leads(engine, CFG, "b1", *SEP, flow="web").value == 2             # 1 и 31


def test_mql_field_check_is_over_the_whole_window_not_the_flow_slice(engine):
    """Ревью 1.6.0: в потоке web одна закрытая сделка без полей, в no_visit поля есть. Проверка загрузки — по всей
    выборке, поэтому срез потока не становится «нет данных», и потоки складываются в «all»."""
    load(engine, [d(1, status="143", route=None), d(2, status="143", route="Маршрут А", ),
                  {**d(3, status="143"), "close_reason": "Причина 1"}])
    tracked(engine, "b1", visits_rows=[v(101, 5, "seo")], order_rows=[marker(1, 101), marker(2, None), marker(3, None)])
    a, w, n = (mql(engine, CFG, "b1", *SEP, flow=f) for f in ("all", "web", "no_visit"))
    assert w.value == 0 and w.status is not Status.NO_DATA, w.missing
    assert w.value + n.value == a.value == 1


def test_contour_and_company_sum_keep_the_flow(engine, tmp_path):
    import yaml
    raw = yaml.safe_load(SYNTHETIC.read_text(encoding="utf-8"))
    raw.setdefault("cross_scope", {})["paid_deals_crm"] = {"allowed": True, "proof": "сделка принадлежит одному бренду"}
    path = tmp_path / "instance.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    summed = load_config(path)
    world(engine)
    for scope in ("b1", "company"):
        for contour in ("one_per_contact", "with_content"):
            a, w, n = (new_first_sql(engine, CFG, scope, *SEP, segment=f"contour={contour}", flow=f)
                       for f in ("all", "web", "no_visit"))
            assert w.value + n.value == a.value and w.segment == f"contour={contour}", (scope, contour)
    a, w, n = (paid_deals_crm(engine, summed, "company", *SEP, flow=f) for f in ("all", "web", "no_visit"))
    assert (w.flow, n.flow) == ("web", "no_visit") and w.value + n.value == a.value == 2


def test_leads_without_rule_and_unknown_flow_are_refused(engine, tmp_path):
    import yaml
    raw = yaml.safe_load(SYNTHETIC.read_text(encoding="utf-8"))
    raw.pop("leads")
    path = tmp_path / "instance.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuleViolation, match="leads.pipelines"):
        leads(engine, load_config(path), "b1", *SEP)
    with pytest.raises(RuleViolation, match="поток «site»"):
        mql(engine, CFG, "b1", *SEP, flow="site")


def test_publication_gives_flows_only_to_deal_metrics_and_keeps_all_rows(engine):
    world(engine)
    before = collect(engine, CFG, [SEP], ("visits", "mql", "new_first_sql"), ["b1"], ["", "contour=digital"])
    rows = collect(engine, CFG, [SEP], ("visits", "mql", "new_first_sql"), ["b1"], ["", "contour=digital"],
                   ["all", "web", "no_visit"])
    added = [r for r in rows if r["metric"] in FLOW_SUPPORT and r["flow"] != "all"]
    assert [r for r in rows if r not in added] == before                             # прежние строки не меняются
    assert [r["flow"] for r in rows if r["metric"] == "visits"] == ["web"]          # у визитов поток свой, один
    assert not [r for r in added if r["segment"]]                                    # поток — только без разреза
    assert {(r["metric"], r["flow"]) for r in added} == {
        ("mql", "web"), ("mql", "no_visit"), ("new_first_sql", "web"), ("new_first_sql", "no_visit")}
    with pytest.raises(RuleViolation, match="site"):                              # опечатка в потоке — отказ сразу
        collect(engine, CFG, [SEP], ("mql",), ["b1"], None, ["all", "site"])
