-- 0003: история изменений и оплаты больше не держат внешний ключ на facts.deal. В DuckDB повторный upsert сделки,
-- на которую уже ссылается история, падает: «key is still referenced by a foreign key in a different table»
-- (ревью этапа 2, 15.09.2026) — со второго прогона инкремент сделок перестал бы работать. Связь проверяет писатель
-- (К4), как для журнала загрузок. Таблицы пересоздаются одним кодом на обоих движках, данные переносятся полностью.
CREATE TABLE facts.deal_field_change_v3 (
  deal_id BIGINT NOT NULL,
  field VARCHAR NOT NULL,
  changed_at TIMESTAMP WITH TIME ZONE NOT NULL,
  old_value VARCHAR,
  new_value VARCHAR,
  recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (deal_id, field, changed_at),
  CONSTRAINT k5_change_not_future CHECK (changed_at <= loaded_at)
);
INSERT INTO facts.deal_field_change_v3 (deal_id, field, changed_at, old_value, new_value, recorded_at, source_system,
  source_class, load_id, loaded_at)
SELECT deal_id, field, changed_at, old_value, new_value, recorded_at, source_system, source_class, load_id, loaded_at
FROM facts.deal_field_change;
DROP TABLE facts.deal_field_change;
ALTER TABLE facts.deal_field_change_v3 RENAME TO deal_field_change;

CREATE TABLE facts.payment_v3 (
  payment_id VARCHAR PRIMARY KEY,
  deal_id BIGINT NOT NULL,
  paid_at TIMESTAMP WITH TIME ZONE NOT NULL,
  revenue DECIMAL(18,2) NOT NULL,
  cogs DECIMAL(18,2),
  money_system VARCHAR NOT NULL,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  CONSTRAINT k5_payment_not_future CHECK (paid_at <= loaded_at)
);
INSERT INTO facts.payment_v3 (payment_id, deal_id, paid_at, revenue, cogs, money_system, source_system, source_class,
  load_id, loaded_at)
SELECT payment_id, deal_id, paid_at, revenue, cogs, money_system, source_system, source_class, load_id, loaded_at
FROM facts.payment;
DROP TABLE facts.payment;
ALTER TABLE facts.payment_v3 RENAME TO payment;
