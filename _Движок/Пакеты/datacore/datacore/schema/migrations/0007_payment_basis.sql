-- Этап 4. Есть ли у платежа документ-основание (счёт, заказ, отгрузка). Платёж без основания не привязан ни к какой
-- продаже: это перевод эквайринга по реестру или разбор банковской выписки. В выручку по сделкам он не входит, иначе
-- те же деньги считаются дважды — сначала покупка клиента, потом перевод банка одной строкой за много покупок.
--
-- Живая сверка первого инстанса 16.09.2026 с P&L финансового контура: без этого правила месяцы с такими
-- переводами расходились на десятки процентов, с ним — в пределах нескольких процентов. Такие платежи бывают
-- не каждый месяц: правило проверяется на всей истории, а не на одном месяце.
--
-- Таблица пересоздаётся, а не меняется через ALTER: DuckDB не умеет добавлять колонку с ограничением NOT NULL
-- («Adding columns with constraints not yet supported»). Деньги после миграции перезагружаются целиком — повтор
-- прогона не задваивает их, платёж заменяется по своему номеру.
DROP TABLE IF EXISTS facts.payment;
CREATE TABLE facts.payment (
  payment_id VARCHAR NOT NULL,
  deal_id BIGINT,
  order_id VARCHAR,
  brand VARCHAR,
  paid_at TIMESTAMP WITH TIME ZONE NOT NULL,
  revenue DECIMAL(18,2) NOT NULL,
  cogs DECIMAL(18,2),
  has_basis BOOLEAN NOT NULL,
  money_system VARCHAR NOT NULL,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (payment_id, source_system),
  CONSTRAINT k5_payment_not_future CHECK (paid_at <= loaded_at)
);
