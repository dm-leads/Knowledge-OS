-- Этап 6. Рекламный расход по дням и кампаниям: обе суммы — без НДС и с НДС — и ставка НДС периода.
--
-- Одной суммы с признаком «включает НДС» мало: ставка менялась (до декабря 2025 включительно 20 %, с января
-- 2026 — 22 %), и пересчитать одну сумму в другую постоянным множителем значило бы испортить всю историю до
-- смены ставки. Поэтому источник спрашивается дважды, а ставка выводится по факту месяца: сумма с НДС, делённая
-- на сумму без НДС, минус единица. Подённое деление на копеечных суммах даёт мусор округления (19,6 % вместо 20).
--
-- `cost` — сумма без НДС, `cost_includes_vat` всегда ложь: это экономический расход, который сверяется с
-- финансовым отчётом. `cost_with_vat` — то, что показывает система сквозной аналитики и что уходит со счёта.
--
-- `campaign` — номер кампании источника, а не название: название меняют, номер постоянен. Название и тип лежат
-- рядом для чтения человеком и для будущего деления расхода по брендам.
--
-- Дней без расхода в таблице нет: ноль как факт доказывается журналом окон (meta.window_log), а не пустой
-- строкой с выдуманной кампанией. Строка «кампания ноль» исказила бы число кампаний и деление по ним.
--
-- Таблица пересоздаётся, а не меняется через ALTER: DuckDB не умеет добавлять колонку с NOT NULL. До этой
-- миграции загрузчика расхода не было, таблица пуста — данные не теряются.
DROP TABLE IF EXISTS facts.spend;
CREATE TABLE facts.spend (
  day DATE NOT NULL,
  campaign VARCHAR NOT NULL,
  campaign_name VARCHAR NOT NULL,
  campaign_type VARCHAR,
  cost DECIMAL(18,2) NOT NULL,
  cost_includes_vat BOOLEAN NOT NULL,
  cost_with_vat DECIMAL(18,2) NOT NULL,
  vat_rate DECIMAL(5,2) NOT NULL,
  clicks INTEGER,
  impressions INTEGER,
  source_system VARCHAR NOT NULL,
  source_class VARCHAR NOT NULL CHECK (source_class IN ('A','B','C','D','E','F')),
  load_id VARCHAR NOT NULL,
  loaded_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (day, campaign, source_system),
  CONSTRAINT spend_vat_not_below CHECK (cost_with_vat >= cost AND cost >= 0)
);
