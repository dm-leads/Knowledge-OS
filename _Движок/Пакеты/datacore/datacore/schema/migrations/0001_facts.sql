-- 0001: схемы, журнал загрузок, факты с происхождением, таблица чисел. Один код на DuckDB и Postgres.
-- Отклонение от первой редакции схемы в стандарте (draft): meta.load_log вместо «load», facts.phone_call
-- вместо «call» — LOAD и CALL суть операторы движков; стандарт приводится к этим именам на воротах этапа.
CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS facts;
CREATE SCHEMA IF NOT EXISTS metrics;

CREATE TABLE IF NOT EXISTS meta.schema_version (
  version INTEGER PRIMARY KEY,
  name VARCHAR NOT NULL,
  applied_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Журнал загрузок: load_id обязан существовать здесь раньше любой строки фактов. К1 проверяет писатель
-- (регистрация и совпадение системы и класса): внешний ключ между схемами DuckDB не поддерживает.
CREATE TABLE IF NOT EXISTS meta.load_log (
  load_id VARCHAR PRIMARY KEY,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  as_of DATE NOT NULL,
  started_at TIMESTAMP WITH TIME ZONE NOT NULL,
  finished_at TIMESTAMP WITH TIME ZONE,
  rows_read INTEGER,
  rows_written INTEGER,
  status VARCHAR NOT NULL CHECK (status IN ('running','ok','failed'))
);

CREATE TABLE IF NOT EXISTS facts.contact (
  contact_id BIGINT PRIMARY KEY,
  brand VARCHAR NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  declared_source VARCHAR,
  phone_hash VARCHAR,
  email_hash VARCHAR,
  deleted_at TIMESTAMP WITH TIME ZONE,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  CONSTRAINT k5_contact_not_future CHECK (created_at <= loaded_at)
);

CREATE TABLE IF NOT EXISTS facts.deal (
  deal_id BIGINT PRIMARY KEY,
  contact_id BIGINT NOT NULL,
  brand VARCHAR NOT NULL,
  pipeline VARCHAR NOT NULL,
  status VARCHAR NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  first_sql_at TIMESTAMP WITH TIME ZONE,
  is_new_first BOOLEAN NOT NULL DEFAULT FALSE,
  closed_at TIMESTAMP WITH TIME ZONE,
  price DECIMAL(18,2),
  entry_channel_tech VARCHAR,
  entry_channel_summary VARCHAR,
  marker VARCHAR,
  parent_deal_id BIGINT,
  deleted_at TIMESTAMP WITH TIME ZONE,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  CONSTRAINT k4_deal_contact FOREIGN KEY (contact_id) REFERENCES facts.contact(contact_id),
  CONSTRAINT k5_deal_not_future CHECK (created_at <= loaded_at)
);

CREATE TABLE IF NOT EXISTS facts.deal_field_change (
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
  CONSTRAINT k4_change_deal FOREIGN KEY (deal_id) REFERENCES facts.deal(deal_id),
  CONSTRAINT k5_change_not_future CHECK (changed_at <= loaded_at)
);

CREATE TABLE IF NOT EXISTS facts.payment (
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
  CONSTRAINT k4_payment_deal FOREIGN KEY (deal_id) REFERENCES facts.deal(deal_id),
  CONSTRAINT k5_payment_not_future CHECK (paid_at <= loaded_at)
);

CREATE TABLE IF NOT EXISTS facts.visit (
  visit_id BIGINT PRIMARY KEY,
  started_at TIMESTAMP WITH TIME ZONE NOT NULL,
  marker VARCHAR,
  marker_level_1 VARCHAR,
  landing_page VARCHAR,
  referrer VARCHAR,
  client_id VARCHAR,
  device VARCHAR,
  geo VARCHAR,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  CONSTRAINT k5_visit_not_future CHECK (started_at <= loaded_at)
);

CREATE TABLE IF NOT EXISTS facts.phone_call (
  call_id BIGINT PRIMARY KEY,
  started_at TIMESTAMP WITH TIME ZONE NOT NULL,
  visit_id BIGINT,
  scenario VARCHAR,
  callee_line VARCHAR,
  caller_hash VARCHAR,
  deal_id BIGINT,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  CONSTRAINT k5_call_not_future CHECK (started_at <= loaded_at)
);

CREATE TABLE IF NOT EXISTS facts.order_marker (
  deal_id BIGINT NOT NULL,
  marker VARCHAR NOT NULL,
  visit_id BIGINT,
  ordered_at TIMESTAMP WITH TIME ZONE NOT NULL,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (deal_id, source_system),
  CONSTRAINT k5_marker_not_future CHECK (ordered_at <= loaded_at)
);

CREATE TABLE IF NOT EXISTS facts.web_daily (
  day DATE NOT NULL,
  landing_page VARCHAR NOT NULL,
  goal VARCHAR NOT NULL DEFAULT '',
  visits INTEGER NOT NULL,
  visitors INTEGER,
  goal_reaches INTEGER,
  robots INTEGER,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (day, landing_page, goal, source_system)
);

CREATE TABLE IF NOT EXISTS facts.spend (
  day DATE NOT NULL,
  campaign VARCHAR NOT NULL,
  cost DECIMAL(18,2) NOT NULL,
  cost_includes_vat BOOLEAN NOT NULL,
  clicks INTEGER,
  impressions INTEGER,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (day, campaign, source_system)
);

CREATE TABLE IF NOT EXISTS facts.demand (
  month DATE NOT NULL,
  phrase VARCHAR NOT NULL,
  region VARCHAR NOT NULL,
  frequency INTEGER NOT NULL,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (month, phrase, region, source_system)
);

CREATE TABLE IF NOT EXISTS facts.dialog_fact (
  fact_code VARCHAR NOT NULL,
  value VARCHAR NOT NULL,
  channel VARCHAR NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  observations INTEGER NOT NULL,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (fact_code, value, channel, period_start, period_end, source_system)
);

-- Агрегат внешней системы с датой съёма: только для мостов и известных ответов (Я1).
CREATE TABLE IF NOT EXISTS facts.snapshot_number (
  metric VARCHAR NOT NULL,
  scope VARCHAR NOT NULL,
  flow VARCHAR NOT NULL DEFAULT 'all',
  segment VARCHAR NOT NULL DEFAULT '',
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  as_of DATE NOT NULL,
  value FLOAT8,
  unit VARCHAR NOT NULL DEFAULT 'шт',
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (metric, scope, flow, segment, period_start, period_end, as_of, source_system)
);

CREATE TABLE IF NOT EXISTS facts.exclusion (
  entity VARCHAR NOT NULL,
  entity_key VARCHAR NOT NULL,
  reason VARCHAR NOT NULL,
  since DATE NOT NULL,
  decided_by VARCHAR NOT NULL,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (entity, entity_key)
);

-- Опубликованное число: контракт раздела 4 стандарта. Статус проверяется базой (К2).
CREATE TABLE IF NOT EXISTS metrics.number (
  metric VARCHAR NOT NULL,
  level VARCHAR NOT NULL,
  scope VARCHAR NOT NULL,
  flow VARCHAR NOT NULL,
  segment VARCHAR NOT NULL DEFAULT '',
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  source VARCHAR NOT NULL,
  status VARCHAR NOT NULL CHECK (status IN ('факт','оценка','proxy','нет данных')),
  value FLOAT8,
  denominator VARCHAR,
  unit VARCHAR NOT NULL DEFAULT 'шт',
  missing VARCHAR NOT NULL DEFAULT '',
  as_of DATE NOT NULL,
  model VARCHAR NOT NULL,
  computed_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (metric, level, scope, flow, segment, period_start, period_end, source, as_of),
  CONSTRAINT k2_no_data_is_null CHECK ((status = 'нет данных') = (value IS NULL))
);
