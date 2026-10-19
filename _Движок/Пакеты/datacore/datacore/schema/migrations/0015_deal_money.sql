-- Публикация «деньги по сделкам» для потребителей ядра (агенты, связка разговоров со сделками, движок роста).
-- Таблицы производные: их полностью пересчитывает serve/deal_money.py из фактов тем же отбором, что и метрики
-- revenue и gross_profit, поэтому сумма строк равна метрике. Потребитель берёт деньги отсюда, а не считает их сам:
-- второе определение выручки рядом с метриками разошлось бы с ней при первой правке.
--
-- deal_money_month       — месяц × кабинет бренда (и «none» — деньги без бренда) × сделка (пусто — без сделки).
--                           Строк кабинета компании нет: компания — сумма всех строк.
-- deal_money_month_total — итог месяца и кабинета (с компанией) из метрик того же прогона: значение, статус, пояснение.
-- deal_money             — сделка за всё время на дату съёма: деньги, себестоимость (заказ один раз), признаки CRM.
-- contact_source         — «откуда узнал» у клиента и его категория по словарю конфигурации инстанса.
CREATE TABLE IF NOT EXISTS metrics.deal_money_month (
  month DATE NOT NULL,
  scope VARCHAR NOT NULL,
  deal_id BIGINT,
  revenue DECIMAL(18,2) NOT NULL,
  cogs DECIMAL(18,2) NOT NULL,
  unpriced_revenue DECIMAL(18,2) NOT NULL,
  unshipped_orders INTEGER NOT NULL,
  unshipped_paid DECIMAL(18,2) NOT NULL,
  as_of DATE NOT NULL,
  computed_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS deal_money_month_period ON metrics.deal_money_month (month, scope);
CREATE INDEX IF NOT EXISTS deal_money_month_deal ON metrics.deal_money_month (deal_id);

CREATE TABLE IF NOT EXISTS metrics.deal_money_month_total (
  month DATE NOT NULL,
  scope VARCHAR NOT NULL,
  revenue DECIMAL(18,2),
  revenue_status VARCHAR NOT NULL,
  revenue_missing VARCHAR,
  gross_profit DECIMAL(18,2),
  gross_profit_status VARCHAR NOT NULL,
  gross_profit_missing VARCHAR,
  no_deal_share DECIMAL(9,6),
  coverage_status VARCHAR NOT NULL,
  rows_revenue DECIMAL(18,2) NOT NULL,
  rows_gross_profit DECIMAL(18,2) NOT NULL,
  as_of DATE NOT NULL,
  computed_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (month, scope)
);

CREATE TABLE IF NOT EXISTS metrics.deal_money (
  deal_id BIGINT PRIMARY KEY,
  contact_id BIGINT,
  brand VARCHAR,
  pipeline VARCHAR,
  pipeline_role VARCHAR NOT NULL,
  outcome VARCHAR,
  created_at TIMESTAMP WITH TIME ZONE,
  closed_at TIMESTAMP WITH TIME ZONE,
  close_reason VARCHAR,
  paid_net DECIMAL(18,2) NOT NULL,
  paid BOOLEAN NOT NULL,
  first_paid_at TIMESTAMP WITH TIME ZONE,
  last_paid_at TIMESTAMP WITH TIME ZONE,
  cogs_lifetime DECIMAL(18,2) NOT NULL,
  gross_profit_lifetime DECIMAL(18,2) NOT NULL,
  unpriced_revenue DECIMAL(18,2) NOT NULL,
  unshipped_orders INTEGER NOT NULL,
  as_of DATE NOT NULL,
  computed_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS deal_money_contact ON metrics.deal_money (contact_id);

CREATE TABLE IF NOT EXISTS metrics.contact_source (
  contact_id BIGINT PRIMARY KEY,
  declared_source VARCHAR NOT NULL,
  category VARCHAR NOT NULL,
  as_of DATE NOT NULL,
  computed_at TIMESTAMP WITH TIME ZONE NOT NULL
);
