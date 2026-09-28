"""Метрики денег: выручка в рублях и число оплаченных сделок; деньги без сделки и без бренда видны в пояснении."""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from datacore.schema.config import load_config
from datacore.schema.migrate import migrate
from datacore.schema.number import Status
from datacore.schema.provenance import Provenance
from datacore.schema.writer import finish_load, register_load, write_facts
from datacore.serve.metrics import REGISTRY, payments, revenue
from datacore.tests.helpers import SYNTHETIC

CFG = load_config(SYNTHETIC)
AUG = (date(2026, 8, 1), date(2026, 9, 1))


def money(engine, rows, as_of=date(2026, 9, 16), since=date(2026, 8, 1)):
    """Факты денег вместе с окном покрытия: без окна метрика откажется считать, и это её правильное поведение."""
    from datacore.tests.helpers import log_money_window
    rows = list(rows)
    p = Provenance("ledger", "D", f"L-{uuid4().hex[:8]}", datetime(2026, 9, 16, 12, tzinfo=timezone.utc))
    register_load(engine, p, as_of)
    write_facts(engine, "payment", rows, p, mode="upsert")
    log_money_window(engine, CFG, since, as_of, rows, p.load_id)
    finish_load(engine, p.load_id, rows_read=len(rows), rows_written=len(rows), status="ok")


def pay(pid, deal_id, brand, day, rub, has_basis=True):
    return dict(payment_id=pid, deal_id=deal_id, order_id=f"ord-{pid}", brand=brand,
                paid_at=datetime(2026, 8, day, 12, tzinfo=timezone.utc), revenue=Decimal(str(rub)),
                cogs=None, money_system="ledger", has_basis=has_basis)


def test_revenue_sums_rubles_by_brand_and_company(engine):
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "150000.00"), pay("p2", 102, "b2", 6, "50000.00")])
    assert float(revenue(engine, CFG, "b1", *AUG).value) == 150000.0
    assert float(revenue(engine, CFG, "b2", *AUG).value) == 50000.0
    total = revenue(engine, CFG, "company", *AUG)
    # компания — это все её деньги, а не сумма кабинетов: складывать нечего, поэтому и scope остаётся «company»
    assert float(total.value) == 200000.0 and total.scope == "company"


def test_payments_count_deals_not_documents(engine):
    """Две оплаты одной сделки — это одна оплаченная сделка: иначе число разойдётся с CRM."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00"), pay("p2", 101, "b1", 20, "50000.00"),
                   pay("p3", 103, "b1", 7, "70000.00")])
    assert payments(engine, CFG, "b1", *AUG).value == 2


def test_money_without_deal_is_named_but_does_not_lower_status(engine):
    """Платёж без сделки, но с брендом — деньги кабинета: в его выручке он есть, в выручке по сделкам — нет. Это
    объявленный отбор определения, а не неопределённость: назван суммой, статус «факт» (ревью методологии 28.09.2026).
    Счётчик считается тем же отбором, что и число: у второго кабинета этих денег в пояснении нет."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00"), pay("p2", None, "b1", 6, "30000.00")])
    n = revenue(engine, CFG, "b1", *AUG)
    assert float(n.value) == 130000.0 and n.status is Status.FACT, n.missing
    assert "платежей без сделки на 30 000" in n.missing
    other = revenue(engine, CFG, "b2", *AUG)
    assert "без сделки" not in other.missing and other.status is Status.FACT, other.missing


def test_declared_selections_keep_fact_and_uncertainty_lowers_it(engine):
    """Без основания, без сделки, возврат без сделки — названы суммой, статус «факт». Настоящая неопределённость —
    «оценка»: у кабинета деньги без бренда (могли быть его), у любого числа — незавершённая загрузка после успешной.
    Проверяются оба случая: правка, «улучшающая» статус, без второго случая невидима (стандарт, раздел 4)."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00"),
                   pay("p2", 101, "b1", 6, "2500000.00", has_basis=False),
                   pay("p3", None, "b1", 7, "30000.00"),
                   pay("p4", None, "b1", 8, "-4000.00")])
    for scope in ("b1", "company"):
        n = revenue(engine, CFG, scope, *AUG)
        assert n.status is Status.FACT, (scope, n.missing)
        assert "без документа-основания" in n.missing and "2500000" in n.missing.replace(" ", "")
        assert "платежей без сделки" in n.missing and "возвратов без сделки" in n.missing

    money(engine, [pay("p5", None, None, 9, "7000.00")])               # деньги без бренда
    b1, company = revenue(engine, CFG, "b1", *AUG), revenue(engine, CFG, "company", *AUG)
    assert b1.status is Status.ESTIMATE and "денег без бренда на 7 000" in b1.missing, b1.missing
    assert float(b1.value) == 126000.0                                 # в число кабинета они не вошли
    assert company.status is Status.FACT and "в том числе без бренда 7 000" in company.missing, company.missing
    assert float(company.value) == 133000.0                            # у компании — вошли

    broken = Provenance("ledger", "D", f"L-{uuid4().hex[:8]}", datetime(2026, 9, 17, 12, tzinfo=timezone.utc))
    register_load(engine, broken, date(2026, 9, 17))
    finish_load(engine, broken.load_id, rows_read=0, rows_written=0, status="failed")
    n = revenue(engine, CFG, "company", *AUG)
    assert n.status is Status.ESTIMATE and "не завершена" in n.missing, n.missing


def test_period_outside_coverage_is_no_data_not_zero(engine):
    """Май не загружали — значит выручка мая не «0 ₽», а «нет данных». Иначе ноль сойдёт за факт."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00")])
    n = revenue(engine, CFG, "b1", date(2026, 5, 1), date(2026, 6, 1))
    assert n.status is Status.NO_DATA and n.value is None and "не покрыт" in n.missing


def test_registry_has_money_metrics():
    assert {"revenue", "payments"} <= set(REGISTRY)


def test_refund_without_deal_is_named_separately(engine):
    """Возврат без сделки и платёж без сделки — разные вещи, и гасить друг друга в пояснении они не должны.
    Живой случай 16.09.2026: все четыре возврата сентября пришли по розничным отгрузкам без заказа."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00"),
                   pay("p2", None, "b1", 6, "30000.00"),            # платёж без сделки
                   pay("r1", None, "b1", 7, "-20000.00")])          # возврат без сделки
    n = revenue(engine, CFG, "b1", *AUG)
    assert float(n.value) == 110000.0
    text = n.missing.replace(" ", "")
    assert "платежейбезсделкина30000" in text, n.missing
    assert "возвратовбезсделкина20000" in text, n.missing


def test_payment_without_basis_is_not_revenue(engine):
    """Перевод эквайринга по реестру — те же деньги, уже посчитанные по покупкам клиентов. В выручку он не входит,
    но и не пропадает молча: сумма названа в пояснении.

    Живая сверка 16.09.2026: без правила месяцы с такими переводами расходились с P&L на десятки процентов."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00"),
                   pay("p2", 101, "b1", 6, "2500000.00", has_basis=False)])
    n = revenue(engine, CFG, "b1", *AUG)
    assert float(n.value) == 100000.0
    assert "без документа-основания" in n.missing and "2 500 000" in n.missing


def test_payment_without_basis_is_not_a_paid_deal(engine):
    """Сделка, у которой есть только перевод по реестру, оплаченной не считается."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00"),
                   pay("p2", 202, "b1", 6, "50000.00", has_basis=False)])
    assert payments(engine, CFG, "b1", *AUG).value == 1


def test_paid_deals_crm_counts_won_deals_by_close_date(engine):
    """Отдельная метрика под определение известного ответа: сделки CRM в выигрышном статусе указанных воронок,
    по дате закрытия. Это НЕ payments: там сделки, по которым пришли деньги, по всем воронкам и любому статусу.

    Живая проверка 16.09.2026: по этому определению ядро даёт ровно 679 и 49 — как в известном ответе, а
    payments за тот же период даёт 976 и 177."""
    from datacore.serve.metrics import paid_deals_crm
    from datacore.schema.writer import register_load as reg
    migrate(engine)
    p = Provenance("crm_mirror", "D", "L-crm", datetime(2026, 9, 16, 12, tzinfo=timezone.utc))
    reg(engine, p, date(2026, 9, 16))
    write_facts(engine, "contact", [dict(contact_id=7, brand="b1",
                                         created_at=datetime(2026, 5, 1, tzinfo=timezone.utc))], p, mode="upsert")
    deals = [
        dict(deal_id=1, contact_id=7, brand="b1", pipeline="100", status="142",
             created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), closed_at=datetime(2026, 8, 5, tzinfo=timezone.utc)),
        dict(deal_id=2, contact_id=7, brand="b1", pipeline="100", status="143",   # проигранная
             created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), closed_at=datetime(2026, 8, 6, tzinfo=timezone.utc)),
        dict(deal_id=3, contact_id=7, brand="b1", pipeline="9999999", status="142",   # чужая воронка
             created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), closed_at=datetime(2026, 8, 7, tzinfo=timezone.utc)),
        dict(deal_id=4, contact_id=7, brand="b1", pipeline="100", status="142",   # закрыта вне окна
             created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), closed_at=datetime(2026, 9, 5, tzinfo=timezone.utc)),
    ]
    write_facts(engine, "deal", deals, p, mode="upsert")
    finish_load(engine, p.load_id, rows_read=len(deals), rows_written=len(deals), status="ok")
    n = paid_deals_crm(engine, CFG, "b1", *AUG)
    assert n.value == 1, n.missing


def test_revenue_counts_only_its_own_money_system(engine):
    """Ревью Codex (п.6): в запросах не было фильтра по системе. Деньги другой финансовой системы — например,
    исторические или из параллельного контура — складывались бы в одно число с нашими."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00")])
    p = Provenance("other_money", "D", f"L-{uuid4().hex[:8]}", datetime(2026, 9, 16, 12, tzinfo=timezone.utc))
    register_load(engine, p, date(2026, 9, 16))
    write_facts(engine, "payment", [dict(pay("p9", 999, "b1", 7, "777000.00"), money_system="other_money")],
                p, mode="upsert")
    finish_load(engine, p.load_id, rows_read=1, rows_written=1, status="ok")
    assert float(revenue(engine, CFG, "b1", *AUG).value) == 100000.0
    assert payments(engine, CFG, "b1", *AUG).value == 1


def test_company_revenue_includes_money_without_brand(engine):
    """Выручка компании — это все деньги компании, а не сумма кабинетов.

    Платёж по заказу без поля «Компания» принадлежит компании, хотя кабинет ему не приписан. Складывая только
    кабинеты, ядро занижало выручку: живая сверка 16.09.2026 дала расхождение с P&L в другую сторону, чем по всем
    деньгам, — выпадали платежи заказов без поля бренда."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00"),
                   pay("p2", 102, "b2", 6, "50000.00"),
                   pay("p3", 103, None, 7, "30000.00")])          # заказ без поля «Компания»
    total = revenue(engine, CFG, "company", *AUG)
    assert float(total.value) == 180000.0
    assert "без бренда" in total.missing and "30 000" in total.missing


def test_scope_revenue_still_counts_only_its_own_brand(engine):
    """Кабинету чужие и безбрендовые деньги по-прежнему не приписываются — но безбрендовые названы, и число кабинета
    становится «оценкой»: чьи они, неизвестно (ревью переноса 28.09.2026 — раньше кабинет молчал о них при «факте»)."""
    migrate(engine)
    money(engine, [pay("p1", 101, "b1", 5, "100000.00"), pay("p3", 103, None, 7, "30000.00")])
    n = revenue(engine, CFG, "b1", *AUG)
    assert float(n.value) == 100000.0
    assert n.status is Status.ESTIMATE and "денег без бренда на 30 000" in n.missing, n.missing
