-- Витрина по каналам, разложенная по месяцам, — источник для отчётных инструментов (дашбордов).
--
-- Это производная таблица, а не факты: каждую строку пересчитывает serve/showcase.py из фактов тем же кодом,
-- что печатает витрину в консоли. Отдельная формула на SQL для дашборда дала бы два определения одного числа,
-- которые со временем разошлись бы. Строки месяца пересобираются целиком: удалить месяц кабинета → записать.
--
-- Пустое значение — «нет данных», а не ноль: например, расход есть только у канала рекламного кабинета.
CREATE TABLE IF NOT EXISTS metrics.channel_month (
  month DATE NOT NULL,
  scope VARCHAR NOT NULL,
  channel_group VARCHAR NOT NULL,
  visits INTEGER,
  leads INTEGER,
  cost DECIMAL(18,2),
  cost_with_vat DECIMAL(18,2),
  cost_per_lead DECIMAL(18,2),
  revenue_leads DECIMAL(18,2),
  gross_profit_leads DECIMAL(18,2),
  gross_profit_minus_cost DECIMAL(18,2),
  note VARCHAR,
  computed_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (month, scope, channel_group)
);
