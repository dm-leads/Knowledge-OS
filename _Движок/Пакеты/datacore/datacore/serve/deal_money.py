"""Публикация «деньги по сделкам»: выручка и себестоимость, разложенные по сделкам, для потребителей ядра.

Зачем. Потребителю (агенту с разметкой разговоров, движку роста) нужны деньги не только итогом месяца, но и по
сделкам: «сколько выручки у сделок, где клиенты жаловались». Посчитай он их сам — рядом с метриками появилось бы второе
определение выручки, и при первой правке правил ядра его суммы разошлись бы с отчётами молча. Поэтому деньги по
сделкам раскладывает само ядро тем же отбором, что и метрики (`money_where`, `paid_orders`), и проверяет при каждом
построении: сумма строк месяца равна `revenue` и `gross_profit` до копейки. Нарушение — сбой шага, публикация прошлой
ночи остаётся на месте.

Правила разложения:
- выручка месяца — по платежам месяца (с возвратами и платежами без заказа), строка на сделку; без сделки — пустая
  сделка; кабинет — бренд платежа, деньги без бренда — кабинет «none». Строк компании нет: компания — сумма всех строк;
- себестоимость месяца — заказы, оплаченные в месяце (как в метрике: заказ один раз в периоде, кабинет платежа);
- сделка заказа — сделка его поступлений с наибольшей суммой, при равенстве — больший номер; заказ, ни у одного
  поступления которого нет сделки, — без сделки. Без этого правила себестоимость заказа с платежами двух сделок
  раздвоилась бы или потерялась;
- за всё время по сделке — чистая сумма оплат (`paid_net`), признак «оплачена» (`paid_net` больше нуля), первая и
  последняя дата поступления (возврат их не сдвигает), себестоимость заказов сделки — каждый заказ один раз
  (`gross_profit_lifetime`; это не сумма месячных `gross_profit`: себестоимость заказа с оплатами в двух месяцах входит
  в оба месяца);
- признаки сделки из CRM — роль воронки (продажа, квалификация, прочее) и исход по статусам конфигурации инстанса:
  номеров воронок и кодов статусов потребитель у себя не держит;
- «откуда узнал» клиента — с категорией по словарю конфигурации (точные значения и префиксы); значение, не найденное
  в словаре, получает категорию по умолчанию и называется в выводе шага.

Шаг ночного прогона после снимков: `python -m datacore.serve.deal_money --as-of <день>`.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from datacore.schema.errors import RuleViolation
from datacore.schema.number import STATUS_RANK, Status

CONFIG = Path(__file__).resolve().parents[3] / "Ядро данных" / "Конфигурация инстанса.yaml"
NO_BRAND = "none"
CENT = Decimal("0.01")
BATCH = 10_000
TABLES = ("metrics.deal_money_month", "metrics.deal_money_month_total", "metrics.deal_money", "metrics.contact_source")
MONTH_COLUMNS = ("month", "scope", "deal_id", "revenue", "cogs", "unpriced_revenue", "unshipped_orders",
                 "unshipped_paid", "as_of", "computed_at")
TOTAL_COLUMNS = ("month", "scope", "revenue", "revenue_status", "revenue_missing", "gross_profit",
                 "gross_profit_status", "gross_profit_missing", "no_deal_share", "coverage_status", "rows_revenue",
                 "rows_gross_profit", "as_of", "computed_at")
DEAL_COLUMNS = ("deal_id", "contact_id", "brand", "pipeline", "pipeline_role", "outcome", "created_at", "closed_at",
                "close_reason", "paid_net", "paid", "first_paid_at", "last_paid_at", "cogs_lifetime",
                "gross_profit_lifetime", "unpriced_revenue", "unshipped_orders", "as_of", "computed_at")
SOURCE_COLUMNS = ("contact_id", "declared_source", "category", "as_of", "computed_at")


def _d(x) -> Decimal:
    return Decimal("0") if x is None else Decimal(str(x))


def _months(first: date, last: date):
    """Месяцы с first по last включительно: (начало, начало следующего)."""
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        nxt = (y + 1, 1) if m == 12 else (y, m + 1)
        yield date(y, m, 1), date(*nxt, 1)
        y, m = nxt


@dataclass
class Report:
    as_of: date
    written: dict[str, int] = field(default_factory=dict)
    read_back: dict[str, int] = field(default_factory=dict)
    checked: int = 0
    skipped: list[str] = field(default_factory=list)
    unknown_sources: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"деньги по сделкам на {self.as_of:%d.%m.%Y}: записано {sum(self.written.values())} строк, "
                 f"прочитано обратно {sum(self.read_back.values())}"]
        lines += [f"  {t}: {self.written.get(t, 0)} / {self.read_back.get(t, 0)}" for t in TABLES]
        lines.append(f"  равенство с метриками проверено: {self.checked} чисел")
        if self.skipped:
            lines.append(f"  не проверено (у метрики нет значения): {len(self.skipped)} — " + ", ".join(self.skipped[:6])
                         + (" …" if len(self.skipped) > 6 else ""))
        if self.unknown_sources:
            lines.append("  ⚠️ значения «Источника» не из словаря, отнесены к категории по умолчанию: "
                         + ", ".join(f"«{k}» ({n})" for k, n in sorted(self.unknown_sources.items())))
        return "\n".join(lines)


def _order_deal(engine, system: str, lo: datetime, hi: datetime) -> dict:
    """Сделка заказа: сделка его поступлений с наибольшей суммой, при равенстве — больший номер; без сделки — None."""
    from datacore.serve.metrics import money_where
    where, args = money_where(system, lo, hi, alias="p")
    best: dict = {}
    for order_id, deal_id, paid in engine.fetchall(
            f"SELECT p.order_id, p.deal_id, SUM(p.revenue) FROM facts.payment p "
            f"WHERE {where} AND p.order_id IS NOT NULL AND p.revenue > 0 GROUP BY p.order_id, p.deal_id", args):
        if deal_id is None:
            best.setdefault(order_id, None)
            continue
        cur = best.get(order_id)
        if cur is None or (_d(paid), deal_id) > (cur[1], cur[0]):
            best[order_id] = (deal_id, _d(paid))
    return {o: (v[0] if v else None) for o, v in best.items()}


def _order_cogs(engine, system: str) -> dict:
    from datacore.serve.metrics import ORDER_COGS
    return {o: (_d(c), _d(u)) for o, c, u in engine.fetchall(ORDER_COGS, (system,))}


def _month_rows(engine, system, lo, hi, scope, order_deal, order_cogs) -> dict:
    """Строки одного месяца и кабинета: сделка → [выручка, себестоимость, без цены, заказов без отгрузки, их оплаты]."""
    from datacore.serve.metrics import money_where, paid_orders
    brand, no_brand = (None, True) if scope == NO_BRAND else (scope, False)
    rows: dict = {}

    def row(deal_id):
        return rows.setdefault(deal_id, [Decimal("0"), Decimal("0"), Decimal("0"), 0, Decimal("0")])

    where, args = money_where(system, lo, hi, brand, no_brand=no_brand)
    for deal_id, rev in engine.fetchall(f"SELECT deal_id, SUM(revenue) FROM facts.payment WHERE {where} GROUP BY deal_id",
                                        args):
        row(deal_id)[0] += _d(rev)
    sql, args = paid_orders(system, lo, hi, brand, no_brand=no_brand)
    for order_id, paid in engine.fetchall(sql, args):
        r = row(order_deal.get(order_id))
        if order_id in order_cogs:
            r[1] += order_cogs[order_id][0]
            r[2] += order_cogs[order_id][1]
        else:
            r[3] += 1
            r[4] += _d(paid)
    return rows


def _inflow_share(engine, system, lo, hi, brand) -> Decimal | None:
    """Доля поступлений без сделки в поступлениях периода — мера покрытия денег сделками (возвраты не в счёт)."""
    from datacore.serve.metrics import money_where
    where, args = money_where(system, lo, hi, brand)
    total, orphan = engine.fetchone(
        "SELECT SUM(CASE WHEN revenue > 0 THEN revenue END), SUM(CASE WHEN revenue > 0 AND deal_id IS NULL THEN revenue END) "
        f"FROM facts.payment WHERE {where}", args)
    return None if not total else (_d(orphan) / _d(total)).quantize(Decimal("0.000001"))


def _worse(a: str, b: str) -> str:
    return a if STATUS_RANK[Status(a)] <= STATUS_RANK[Status(b)] else b


def _category(value: str, rules: dict, report: Report) -> str:
    exact = rules.get("exact") or {}
    if value in exact:
        return exact[value]
    for prefix, cat in (rules.get("prefix") or {}).items():
        if value.startswith(prefix):
            return cat
    report.unknown_sources[value] = report.unknown_sources.get(value, 0) + 1
    return rules.get("default", "channel")


def build(engine, cfg, as_of: date | None = None) -> Report:
    """Пересобрать четыре таблицы публикации в одной транзакции и проверить равенство с метриками."""
    from datacore.serve.metrics import (_money_system, gross_profit, money_where, paid_orders, revenue, state_of,
                                        window)
    system = _money_system(cfg)
    state, _ = state_of(engine, (system,))
    if state is None:
        raise RuleViolation("К2", "в журнале загрузок нет успешной загрузки денег — публиковать нечего")
    report = Report(state)
    now = datetime.now(timezone.utc)
    brands = tuple(s for s in cfg.scopes if s != "company")
    all_lo, all_hi = window(date(2000, 1, 1), date(2200, 1, 1), cfg.timezone)
    if (cfg.money or {}).get("history_from"):
        history_from = date.fromisoformat(str(cfg.money["history_from"]))
    else:                                        # без настройки — с месяца первого платежа системы денег
        where, args = money_where(system, all_lo, all_hi)
        first = engine.fetchone(f"SELECT MIN(paid_at) FROM facts.payment WHERE {where}", args)[0]
        history_from = first.date() if first else state
    order_deal = _order_deal(engine, system, all_lo, all_hi)
    order_cogs = _order_cogs(engine, system)
    threshold = (cfg.money or {}).get("fact_max_no_deal_share")

    month_rows, total_rows, problems = [], [], []
    for start, end in _months(history_from, state):
        lo, hi = window(start, end, cfg.timezone)
        where, args = money_where(system, lo, hi)
        # Кабинеты строк — бренды, которые есть в платежах месяца, а не только объявленные: деньги бренда вне
        # конфигурации входят в выручку компании и без своих строк сломали бы равенство без понятной причины.
        present = {b for (b,) in engine.fetchall(f"SELECT DISTINCT brand FROM facts.payment WHERE {where} AND brand IS NOT NULL",
                                                 args)}
        scopes = (*brands, *sorted(present - set(brands)), NO_BRAND)
        # Заказ, оплаченный в месяце платежами двух кабинетов: метрика кабинета берёт его себестоимость в каждом
        # кабинете, метрика компании — один раз. Строки повторяют кабинеты, поэтому у компании разница названа.
        twice = Decimal("0")
        shared = engine.fetchall(f"SELECT p.order_id, COUNT(DISTINCT COALESCE(p.brand, '')) FROM facts.payment p "
                                 f"WHERE {money_where(system, lo, hi, alias='p')[0]} AND p.order_id IS NOT NULL "
                                 "AND p.revenue > 0 GROUP BY p.order_id HAVING COUNT(DISTINCT COALESCE(p.brand, '')) > 1", args)
        for order_id, k in shared:
            twice += order_cogs.get(order_id, (Decimal("0"),))[0] * (int(k) - 1)
        sums: dict[str, tuple[Decimal, Decimal]] = {}
        for scope in scopes:
            rows = _month_rows(engine, system, lo, hi, scope, order_deal, order_cogs)
            rev = sum((r[0] for r in rows.values()), Decimal("0"))
            gp = sum((r[0] - r[1] for r in rows.values()), Decimal("0"))
            sums[scope] = (rev, gp)
            month_rows += [(start, scope, deal_id, r[0], r[1], r[2], r[3], r[4], state, now) for deal_id, r in rows.items()]
        sums["company"] = (sum((v[0] for v in sums.values()), Decimal("0")),
                           sum((v[1] for v in sums.values()), Decimal("0")))
        for scope in (*brands, "company"):
            r, g = revenue(engine, cfg, scope, start, end), gross_profit(engine, cfg, scope, start, end)
            rows_rev, rows_gp = sums[scope]
            gp_missing = g.missing or ""
            expected_gp = rows_gp
            if scope == "company" and shared:
                expected_gp = rows_gp + twice
                note = (f"заказов с оплатами двух кабинетов {len(shared)}: их себестоимость в строках учтена в каждом "
                        f"кабинете, в метрике компании — один раз; строки компании меньше метрики на {twice:.2f} ₽")
                gp_missing = "; ".join(x for x in (gp_missing, note) if x)
                report.skipped.append(f"gross_profit · company · {start:%m.%Y}: {note}")
            for name, number, got in (("revenue", r, rows_rev), ("gross_profit", g, expected_gp)):
                label = f"{name} · {scope} · {start:%m.%Y}"
                if number.value is None:
                    report.skipped.append(label)
                elif abs(_d(number.value) - got) >= CENT:
                    problems.append(f"{label}: метрика {_d(number.value)}, сумма строк {got}")
                else:
                    report.checked += 1
            share = _inflow_share(engine, system, lo, hi, None if scope == "company" else scope)
            coverage = r.status.value
            if threshold is not None and share is not None and share > _d(threshold):
                coverage = _worse(coverage, Status.ESTIMATE.value)
            total_rows.append((start, scope, None if r.value is None else _d(r.value), r.status.value, r.missing or "",
                               None if g.value is None else _d(g.value), g.status.value, gp_missing, share,
                               coverage, rows_rev, rows_gp, state, now))
    if problems:
        raise RuleViolation("К2", "сумма по сделкам не равна метрике — публикация не обновлена: " + "; ".join(problems[:10]))

    # За всё время по сделке: деньги, даты поступлений, себестоимость заказов сделки (каждый заказ один раз).
    where, args = money_where(system, all_lo, all_hi)
    money = {d: [_d(net), first, last] for d, net, first, last in engine.fetchall(
        "SELECT deal_id, SUM(revenue), MIN(CASE WHEN revenue > 0 THEN paid_at END), "
        f"MAX(CASE WHEN revenue > 0 THEN paid_at END) FROM facts.payment WHERE {where} AND deal_id IS NOT NULL "
        "GROUP BY deal_id", args)}
    cost: dict = {}
    sql, args = paid_orders(system, all_lo, all_hi)
    for order_id, _paid in engine.fetchall(sql, args):
        deal_id = order_deal.get(order_id)
        if deal_id is None:
            continue
        c = cost.setdefault(deal_id, [Decimal("0"), Decimal("0"), 0])
        if order_id in order_cogs:
            c[0] += order_cogs[order_id][0]
            c[1] += order_cogs[order_id][1]
        else:
            c[2] += 1
    crm = cfg.crm or {}
    sales = {str(p) for p in crm.get("sales_pipelines", [])}
    qualification = {str(p) for p in (cfg.mql or {}).get("pipelines", [])}
    won, lost = str(crm.get("won_status", "")), str(crm.get("lost_status", ""))
    deals = {}
    pipelines = sorted(sales | qualification)
    if pipelines:
        marks = ", ".join("?" for _ in pipelines)
        for row in engine.fetchall(
                "SELECT deal_id, contact_id, brand, pipeline, status, created_at, closed_at, close_reason FROM facts.deal "
                f"WHERE deleted_at IS NULL AND pipeline IN ({marks})", tuple(pipelines)):
            deals[row[0]] = row
    missing = [d for d in money if d not in deals]
    for i in range(0, len(missing), 1000):
        part = missing[i:i + 1000]
        for row in engine.fetchall(
                "SELECT deal_id, contact_id, brand, pipeline, status, created_at, closed_at, close_reason FROM facts.deal "
                f"WHERE deal_id IN ({', '.join('?' for _ in part)})", tuple(part)):
            deals[row[0]] = row
    deal_rows = []
    for deal_id in sorted(set(deals) | set(money)):
        _, contact_id, brand, pipeline, status, created, closed, reason = deals.get(deal_id, (deal_id, *[None] * 7))
        role = "sales" if str(pipeline) in sales else "qualification" if str(pipeline) in qualification else "other"
        outcome = None if status is None else "won" if str(status) == won else "lost" if lost and str(status) == lost else "open"
        net, first, last = money.get(deal_id, [Decimal("0"), None, None])
        cogs_sum, unpriced, unshipped = cost.get(deal_id, [Decimal("0"), Decimal("0"), 0])
        deal_rows.append((deal_id, contact_id, brand, None if pipeline is None else str(pipeline), role, outcome, created,
                          closed, reason, net, net > 0, first, last, cogs_sum, net - cogs_sum, unpriced, unshipped,
                          state, now))

    rules = crm.get("declared_source_categories") or {}
    source_rows = [(contact_id, value, _category(value, rules, report), state, now) for contact_id, value in
                   ((c, str(v).strip()) for c, v in engine.fetchall(
                       "SELECT contact_id, declared_source FROM facts.contact WHERE declared_source IS NOT NULL "
                       "AND deleted_at IS NULL")) if value]

    batches = {"metrics.deal_money_month": (MONTH_COLUMNS, month_rows),
               "metrics.deal_money_month_total": (TOTAL_COLUMNS, total_rows),
               "metrics.deal_money": (DEAL_COLUMNS, deal_rows),
               "metrics.contact_source": (SOURCE_COLUMNS, source_rows)}
    engine.begin()
    try:
        for table, (columns, rows) in batches.items():
            engine.execute(f"DELETE FROM {table}")
            for i in range(0, len(rows), BATCH):
                engine.bulk_insert(table, columns, rows[i:i + BATCH])
            report.written[table] = len(rows)
        for table in TABLES:
            report.read_back[table] = int(engine.fetchone(f"SELECT COUNT(*) FROM {table}")[0])
        if report.read_back != report.written:
            raise RuntimeError(f"записано {report.written}, прочитано обратно {report.read_back} — молчаливый провал")
        engine.commit()
    except Exception:
        engine.rollback()
        raise
    return report


def main(argv=None) -> int:
    """Шаг ночного прогона: пересобрать публикацию. Код 1 — сбой, нарушение равенства или ноль записанных строк."""
    from datacore.schema.config import load_config
    from datacore.schema.engine import connect
    from datacore.serve.storage import storage_url

    ap = argparse.ArgumentParser(description="Деньги по сделкам: публикация для потребителей ядра")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None, help="день прогона — для сверки с датой съёма")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default=None, help="адрес базы; по умолчанию — из окружения, затем файл DuckDB")
    a = ap.parse_args(argv)
    cfg = load_config(Path(a.config) if a.config else CONFIG)
    try:
        engine = connect(storage_url(cfg, a.db))
        try:
            report = build(engine, cfg, a.as_of)
        finally:
            engine.close()
    except Exception as exc:                          # noqa: BLE001 — любой сбой шага виден кодом возврата
        print(f"🟥 деньги по сделкам не опубликованы: {type(exc).__name__}: {exc}")
        return 1
    print(report.render())
    if a.as_of and report.as_of != a.as_of:
        print(f"⚠️ деньги загружены на {report.as_of:%d.%m.%Y}, а не на {a.as_of:%d.%m.%Y} — публикация подписана датой денег")
    if not report.written.get("metrics.deal_money_month"):
        print("🟥 ни одной строки денег по сделкам — публиковать нечего")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
