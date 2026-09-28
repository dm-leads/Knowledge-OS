-- Этап 3. Счётчик визитов и колл-трекинг выдают id в пределах кабинета: id визитов двух кабинетов пересекаются
-- (проба 15.09.2026) — ключ визита и звонка вместе с системой-источником. Загрузчика визитов и звонков до этапа 3
-- не было, таблицы пусты — пересоздаются. Посетитель хранится только хэшем, у ссылки — только хост.
DROP TABLE IF EXISTS facts.visit;
CREATE TABLE facts.visit (
  visit_id BIGINT NOT NULL,
  started_at TIMESTAMP WITH TIME ZONE NOT NULL,
  marker VARCHAR,
  marker_level_1 VARCHAR,
  landing_page VARCHAR,
  referrer_host VARCHAR,
  client_hash VARCHAR,
  device VARCHAR,
  geo VARCHAR,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (visit_id, source_system),
  CONSTRAINT k5_visit_not_future CHECK (started_at <= loaded_at)
);

DROP TABLE IF EXISTS facts.phone_call;
CREATE TABLE facts.phone_call (
  call_id BIGINT NOT NULL,
  started_at TIMESTAMP WITH TIME ZONE NOT NULL,
  visit_id BIGINT,
  scenario VARCHAR,
  callee_line VARCHAR,
  caller_hash VARCHAR,
  answered BOOLEAN,
  duration_s INTEGER,
  deal_id BIGINT,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (call_id, source_system),
  CONSTRAINT k5_call_not_future CHECK (started_at <= loaded_at)
);

ALTER TABLE facts.order_marker ADD COLUMN marker_level_1 VARCHAR;
