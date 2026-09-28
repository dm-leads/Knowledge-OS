-- Этап 6. Визиты счётчика веб-аналитики (класс A) — сессии, а не визиты трекера рекламы.
--
-- Отдельная таблица, а не facts.visit, по трём причинам.
-- 1. Это другая сущность. Визит трекера (facts.visit) заводится на посетителя и источник перехода: живая сверка
--    первого инстанса — почти у всех посетителей трекера за месяц ровно один визит. Визит счётчика — сессия:
--    тот же человек после 30 минут тишины или вернувшись назавтра даёт новый визит, и визитов заметно больше,
--    чем посетителей. В одной таблице COUNT(*) без фильтра по системе сложил бы
--    несопоставимые вещи.
-- 2. Номер визита счётчика по описанию источника — беззнаковое 64-битное число. Живые номера лежат порядка
--    5·10^18, то есть пока ниже предела BIGINT со знаком (9,22·10^18), но запаса вдвое никто не
--    гарантирует, а переполнение молча испортило бы ключ. Хранится DECIMAL(20,0) — точно на обоих движках (у
--    Postgres нет беззнакового целого). Номер визита трекера (facts.visit) остаётся BIGINT.
-- 3. Колонки: признак нового посетителя, цели визита, метки перехода, число просмотров есть только у счётчика,
--    а маркер канала трекера (marker, marker_level_1) — только у трекера. Общие колонки названы так же, как в
--    facts.visit (started_at, landing_page, referrer_host, client_hash, device, geo), и значат то же самое.
--
-- client_hash — HMAC идентификатора посетителя счётчика ключом инстанса. Трекер визитов хранит тот же
-- идентификатор тем же ключом (facts.visit.client_hash), поэтому визиты двух систем связываются по нему без
-- хранения самого идентификатора.
--
-- Признака робота на уровне визита источник не отдаёт: выгрузка сырых визитов включает роботов, но без отметки.
-- Роботы не выбрасываются: их число по дням пишется в facts.web_daily (колонка robots), и метрика «визиты людей»
-- считается как визиты минус роботы дня.
CREATE TABLE IF NOT EXISTS facts.web_visit (
  visit_id DECIMAL(20,0) NOT NULL,
  started_at TIMESTAMP WITH TIME ZONE NOT NULL,
  client_hash VARCHAR,
  is_new_visitor BOOLEAN,
  traffic_source VARCHAR,          -- тип источника: organic, direct, ad, referral, internal, social, recommend, messenger …
  search_engine VARCHAR,
  adv_engine VARCHAR,
  social_network VARCHAR,
  referrer_host VARCHAR,           -- только хост, как в facts.visit
  utm_source VARCHAR,
  utm_medium VARCHAR,
  utm_campaign VARCHAR,
  utm_content VARCHAR,
  utm_term VARCHAR,
  landing_page VARCHAR,            -- только первый сегмент пути, как в facts.visit: дальше в адресе бывают ПДн
  device VARCHAR,                  -- desktop, mobile, tablet, tv
  geo VARCHAR,                     -- город
  page_views INTEGER,
  duration_s INTEGER,
  goal_reaches INTEGER,            -- сколько раз за визит достигнуты цели (с повторами); какие — в facts.web_visit_goal
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (visit_id, source_system),
  CONSTRAINT k5_web_visit_not_future CHECK (started_at <= loaded_at)
);

-- Цели визита — отдельной таблицей, по строке на цель: у визита бывает два десятка разных целей, и список строкой
-- упирался в предел длины текста К6 (живой прогон августа 2026: 208 знаков). Отбор «визиты с целью N» — простое
-- соединение, а не разбор строки.
CREATE TABLE IF NOT EXISTS facts.web_visit_goal (
  visit_id DECIMAL(20,0) NOT NULL,
  goal_id BIGINT NOT NULL,
  reaches INTEGER NOT NULL,        -- сколько раз цель достигнута за визит
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (visit_id, goal_id, source_system)
);
