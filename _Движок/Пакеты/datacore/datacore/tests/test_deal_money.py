"""Публикация «деньги по сделкам»: сумма строк равна метрикам, сделка и заказ считаются один раз.

Числа проверяются тождествами с метриками ядра, а не записанными значениями: если правило выручки или себестоимости
изменится, разложение обязано измениться вместе с ним — для этого публикация и строится тем же отбором."""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from datacore.schema.config import load_config
from datacore.schema.errors import RuleViolation
from datacore.schema.migrate import migrate
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve import deal_money
from datacore.serve.deal_money import build
from datacore.serve.metrics import gross_profit, revenue
from datacore.tests.helpers import SYNTHETIC, cogs_system, log_money_window

CFG = load_config(SYNTHETIC)
UTC = timezone.utc
AS_OF = date(2026, 10, 5)
AUG, SEP = (date(2026, 8, 1), date(2026, 9, 1)), (date(2026, 9, 1), date(2026, 10, 1))


def at(month, day):
    return datetime(2026, month, day, 12, tzinfo=UTC)


def deal(deal_id, contact_id, pipeline, status):
    return dict(deal_id=deal_id, contact_id=contact_id, brand="b1", pipeline=pipeline, status=status,
                created_at=at(7, 1), first_sql_at=None, is_new_first=False, closed_at=None, price=None,
                entry_channel_tech=None, entry_channel_summary=None, marker=None, parent_deal_id=None,
                deleted_at=None, updated_at=None)


def contact(contact_id, source):
    return dict(contact_id=contact_id, brand="b1", created_at=at(6, 1), declared_source=source, phone_hash=None,
                email_hash=None, deleted_at=None)


def pay(pid, deal_id, when, rub, order_id, brand="b1", has_basis=True):
    return dict(payment_id=pid, deal_id=deal_id, order_id=order_id, brand=brand, paid_at=when,
                revenue=Decimal(str(rub)), cogs=None, money_system="ledger", has_basis=has_basis)


def shipped(order_id, cogs_rub, brand="b1"):
    return dict(shipment_id=f"s-{order_id}", order_id=order_id, invoice_id=None, brand=brand, shipped_at=at(8, 3),
                cogs=Decimal(str(cogs_rub)), revenue_shipped=Decimal(str(cogs_rub)) * 2, positions_total=1,
                positions_priced=1, unpriced_revenue=Decimal("0"), money_system="ledger")


PAYMENTS = [
    pay("p1", 101, at(8, 5), 100000, "ord-1"),
    pay("p2", 101, at(8, 20), 40000, "ord-2"),            # аванс в августе …
    pay("p3", 101, at(9, 10), 60000, "ord-2"),            # … доплата в сентябре по тому же заказу
    pay("p4", 101, at(9, 12), -10000, None),              # возврат без заказа
    pay("p5", None, at(8, 7), 30000, "ord-5"),            # поступление без сделки
    pay("p6", 102, at(8, 8), 20000, "ord-6"),             # заказ с платежами двух сделок: большая сумма — 102
    pay("p7", 104, at(8, 9), 5000, "ord-6"),
    pay("p8", 104, at(8, 10), 7000, "ord-8", brand=None),  # без бренда, без отгрузки
    pay("p9", 105, at(8, 11), 9000, "ord-9", brand="b2"),  # сделки нет в фактах CRM
    pay("p10", 106, at(8, 12), 5000, "ord-10"),           # оплачена …
    pay("p11", 106, at(9, 1), -5000, None),               # … и целиком возвращена
    pay("p12", 101, at(8, 13), 999, "ord-12", has_basis=False),   # без основания — не выручка
]
SHIPMENTS = [shipped("ord-1", 60000), shipped("ord-2", 50000), shipped("ord-5", 10000), shipped("ord-6", 15000),
             shipped("ord-9", 4000, brand="b2"), shipped("ord-10", 3000)]


def load(engine, extra=()):
    migrate(engine)
    payments = PAYMENTS + list(extra)
    p_crm = Provenance(CFG.crm["system_mirror"], "D", f"C-{uuid4().hex[:8]}", datetime(2026, 10, 5, 12, tzinfo=UTC))
    register_load(engine, p_crm, AS_OF)
    write_facts(engine, "contact", [contact(1, "Повторное обращение"), contact(2, "Спросить позже"),
                                    contact(3, "Сайт"), contact(4, None)], p_crm, mode="upsert")
    deals = [deal(101, 1, "100", "142"), deal(102, 2, "100", "143"), deal(103, 3, "300", "143"),
             deal(104, 1, "200", "50"), deal(106, 4, "100", "50")]
    write_facts(engine, "deal", deals, p_crm, mode="upsert")
    finish_load(engine, p_crm.load_id, rows_read=len(deals), rows_written=len(deals), status="ok")
    p = Provenance("ledger", "D", f"M-{uuid4().hex[:8]}", datetime(2026, 10, 5, 12, tzinfo=UTC))
    register_load(engine, p, AS_OF)
    write_facts(engine, "payment", payments, p, mode="upsert")
    write_facts(engine, "shipment_cogs", SHIPMENTS, p, mode="upsert")
    log_money_window(engine, CFG, date(2026, 8, 1), AS_OF, payments, p.load_id)
    log_money_window(engine, CFG, date(2026, 8, 1), AS_OF, SHIPMENTS, p.load_id, system=cogs_system(CFG))
    finish_load(engine, p.load_id, rows_read=len(payments), rows_written=len(payments), status="ok")


def sums(engine, month, scopes):
    marks = ", ".join("?" for _ in scopes)
    rev, cogs = engine.fetchone("SELECT COALESCE(SUM(revenue), 0), COALESCE(SUM(cogs), 0) FROM metrics.deal_money_month "
                                f"WHERE month = ? AND scope IN ({marks})", (month, *scopes))
    return Decimal(str(rev)), Decimal(str(rev)) - Decimal(str(cogs))


def deal_row(engine, deal_id):
    cols = ("pipeline_role", "outcome", "paid_net", "paid", "first_paid_at", "cogs_lifetime", "gross_profit_lifetime",
            "unshipped_orders")
    row = engine.fetchone(f"SELECT {', '.join(cols)} FROM metrics.deal_money WHERE deal_id = ?", (deal_id,))
    return dict(zip(cols, row)) if row else None


@pytest.mark.parametrize("period", [AUG, SEP])
def test_rows_sum_to_revenue_and_gross_profit(engine, period):
    """Сумма строк месяца равна метрикам ядра по каждому кабинету и по компании (все строки, включая «без бренда»)."""
    load(engine)
    build(engine, CFG)
    checked = 0
    for scope, scopes in (("b1", ("b1",)), ("b2", ("b2",)), ("company", ("b1", "b2", "none"))):
        rev, gp = sums(engine, period[0], scopes)
        for got, number in ((rev, revenue(engine, CFG, scope, *period)), (gp, gross_profit(engine, CFG, scope, *period))):
            if number.value is not None:          # «нет данных» у метрики — сравнивать не с чем
                assert got == Decimal(str(number.value)), (scope, number.metric)
                checked += 1
    assert checked >= 4


def test_order_of_two_deals_goes_to_the_larger_one(engine):
    """Себестоимость заказа с платежами двух сделок не раздваивается и не теряется: она у сделки с большей суммой."""
    load(engine)
    build(engine, CFG)
    assert deal_row(engine, 102)["cogs_lifetime"] == Decimal("15000")
    assert deal_row(engine, 104)["cogs_lifetime"] == Decimal("0")


def test_lifetime_counts_each_order_once(engine):
    """Аванс и доплата в разных месяцах: себестоимость заказа в каждом месяце (как у метрики), но за всё время — один раз."""
    load(engine)
    build(engine, CFG)
    r = deal_row(engine, 101)
    assert r["paid_net"] == Decimal("190000") and r["paid"]
    assert r["cogs_lifetime"] == Decimal("110000") and r["gross_profit_lifetime"] == Decimal("80000")
    assert r["pipeline_role"] == "sales" and r["outcome"] == "won"
    aug = engine.fetchone("SELECT cogs FROM metrics.deal_money_month WHERE month = ? AND scope = 'b1' AND deal_id = 101",
                          (AUG[0],))[0]
    sep = engine.fetchone("SELECT cogs FROM metrics.deal_money_month WHERE month = ? AND scope = 'b1' AND deal_id = 101",
                          (SEP[0],))[0]
    assert Decimal(str(aug)) == Decimal("110000") and Decimal(str(sep)) == Decimal("50000")


def test_refunded_deal_is_not_paid_but_keeps_first_payment_date(engine):
    load(engine)
    build(engine, CFG)
    r = deal_row(engine, 106)
    assert r["paid_net"] == Decimal("0") and not r["paid"]
    assert r["first_paid_at"].date() == date(2026, 8, 12)


def test_crm_attributes_come_from_configuration(engine):
    """Роль воронки и исход — по конфигурации инстанса; сделка без фактов CRM остаётся с деньгами, но без признаков."""
    load(engine)
    build(engine, CFG)
    assert deal_row(engine, 102)["outcome"] == "lost"
    assert deal_row(engine, 104)["outcome"] == "open" and deal_row(engine, 104)["unshipped_orders"] == 1
    q = deal_row(engine, 103)
    assert q["pipeline_role"] == "qualification" and q["paid_net"] == Decimal("0")
    other = deal_row(engine, 105)
    assert other["pipeline_role"] == "other" and other["outcome"] is None and other["paid"]


def test_money_without_deal_and_without_brand_have_own_rows(engine):
    load(engine)
    build(engine, CFG)
    no_deal = engine.fetchone("SELECT revenue FROM metrics.deal_money_month WHERE month = ? AND scope = 'b1' "
                              "AND deal_id IS NULL", (AUG[0],))[0]
    assert Decimal(str(no_deal)) == Decimal("30000")
    no_brand = engine.fetchone("SELECT revenue, unshipped_orders FROM metrics.deal_money_month WHERE month = ? "
                               "AND scope = 'none' AND deal_id = 104", (AUG[0],))
    assert Decimal(str(no_brand[0])) == Decimal("7000") and no_brand[1] == 1
    assert engine.fetchone("SELECT COUNT(*) FROM metrics.deal_money_month WHERE scope = 'company'")[0] == 0


def test_month_total_and_coverage_status(engine):
    """Доля поступлений без сделки выше порога конфигурации — месяц не выше «оценки»."""
    load(engine)
    build(engine, CFG)
    share, coverage, status = engine.fetchone(
        "SELECT no_deal_share, coverage_status, revenue_status FROM metrics.deal_money_month_total "
        "WHERE month = ? AND scope = 'b1'", (AUG[0],))
    assert Decimal(str(share)) == Decimal("0.15") and coverage == "оценка"
    assert engine.fetchone("SELECT COUNT(*) FROM metrics.deal_money_month_total WHERE scope = 'company'")[0] >= 2


def test_contact_source_categories(engine):
    load(engine)
    report = build(engine, CFG)
    cats = dict(engine.fetchall("SELECT contact_id, category FROM metrics.contact_source"))
    assert cats == {1: "repeat", 2: "ask_on_sale", 3: "channel"}
    assert report.unknown_sources == {"Сайт": 1}


def test_broken_equality_keeps_previous_publication(engine, monkeypatch):
    """Сумма строк разошлась с метрикой — шаг падает, публикация прошлой ночи остаётся нетронутой."""
    load(engine)
    first = build(engine, CFG)
    real = deal_money._order_cogs
    monkeypatch.setattr(deal_money, "_order_cogs",
                        lambda e, s: {o: (c + 1, u) for o, (c, u) in real(e, s).items()})
    with pytest.raises(RuleViolation):
        build(engine, CFG)
    count = engine.fetchone("SELECT COUNT(*) FROM metrics.deal_money_month")[0]
    assert count == first.written["metrics.deal_money_month"]


def test_insert_failure_keeps_previous_publication(engine, monkeypatch):
    """Строки удалены, вставка упала — транзакция откатывается, прошлая публикация на месте (DuckDB и Postgres)."""
    load(engine)
    first = build(engine, CFG)
    real = engine.bulk_insert

    def failing(table, columns, rows, conflict_key=None):
        if table == "metrics.deal_money":
            raise RuntimeError("сбой вставки")
        return real(table, columns, rows, conflict_key)

    monkeypatch.setattr(engine, "bulk_insert", failing)
    with pytest.raises(RuntimeError):
        build(engine, CFG)
    for table in ("metrics.deal_money_month", "metrics.deal_money"):
        assert engine.fetchone(f"SELECT COUNT(*) FROM {table}")[0] == first.written[table]


def test_order_paid_by_two_brands_is_named_not_fatal(engine):
    """Заказ оплачен в месяце платежами двух кабинетов: кабинеты сходятся с метриками, у компании разница названа."""
    load(engine, extra=[pay("p20", 101, at(8, 21), 1000, "ord-1", brand="b2")])
    report = build(engine, CFG)
    gp_missing, rows_gp = engine.fetchone("SELECT gross_profit_missing, rows_gross_profit FROM metrics.deal_money_month_total "
                                          "WHERE month = ? AND scope = 'company'", (AUG[0],))
    assert "двух кабинетов 1" in gp_missing
    metric = Decimal(str(gross_profit(engine, CFG, "company", *AUG).value))
    assert Decimal(str(rows_gp)) + Decimal("60000") == metric      # себестоимость ord-1 в строках дважды
    assert any("двух кабинетов" in s for s in report.skipped)


def test_brand_outside_configuration_gets_own_rows(engine):
    """Деньги бренда, которого нет в конфигурации, получают свои строки: иначе равенство компании ломалось бы молча."""
    load(engine, extra=[pay("p21", 101, at(8, 22), 2500, "ord-21", brand="b9")])
    build(engine, CFG)
    rev = engine.fetchone("SELECT revenue FROM metrics.deal_money_month WHERE month = ? AND scope = 'b9'", (AUG[0],))[0]
    assert Decimal(str(rev)) == Decimal("2500")
    rows_rev, = sums(engine, AUG[0], ("b1", "b2", "b9", "none"))[:1]
    assert rows_rev == Decimal(str(revenue(engine, CFG, "company", *AUG).value))


def test_written_rows_are_read_back(engine):
    load(engine)
    report = build(engine, CFG)
    assert report.written == report.read_back and report.written["metrics.deal_money"] == 6
    assert report.checked > 0 and report.as_of == AS_OF
