-- Этап 4. Платёж приходит из финансовой системы и ссылается на заказ покупателя; сделка CRM известна не всегда
-- (заказ бывает вне digital-контура). Деньги от этого не перестают быть фактом, поэтому связь со сделкой не
-- обязательна и внешним ключом не проверяется — как история изменений и оплаты в миграции 0003.
DROP TABLE IF EXISTS facts.payment;
CREATE TABLE facts.payment (
  payment_id VARCHAR NOT NULL,
  deal_id BIGINT,
  order_id VARCHAR,
  brand VARCHAR,
  paid_at TIMESTAMP WITH TIME ZONE NOT NULL,
  revenue DECIMAL(18,2) NOT NULL,
  cogs DECIMAL(18,2),
  money_system VARCHAR NOT NULL,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (payment_id, source_system),
  CONSTRAINT k5_payment_not_future CHECK (paid_at <= loaded_at)
);
