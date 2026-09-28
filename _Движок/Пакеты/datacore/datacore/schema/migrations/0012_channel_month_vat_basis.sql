-- Витрина по каналам: расход — по основанию с НДС (решение владельца 23.09.2026: потрачено столько, сколько ушло
-- со счёта). Колонка cost теперь означает расход по основанию spend.cost_basis, сумма без НДС — справочная.
--
-- Таблица производная: её полностью пересчитывает serve/showcase.py из фактов, поэтому пересоздаётся без потерь.
-- Факты (facts.spend) не трогаются: в них по-прежнему лежат обе суммы и ставка НДС.
DROP TABLE IF EXISTS metrics.channel_month;
CREATE TABLE metrics.channel_month (
  month DATE NOT NULL,
  scope VARCHAR NOT NULL,
  channel_group VARCHAR NOT NULL,
  visits INTEGER,
  leads INTEGER,
  cost DECIMAL(18,2),
  cost_without_vat DECIMAL(18,2),
  cost_per_lead DECIMAL(18,2),
  revenue_leads DECIMAL(18,2),
  gross_profit_leads DECIMAL(18,2),
  gross_profit_minus_cost DECIMAL(18,2),
  note VARCHAR,
  computed_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (month, scope, channel_group)
);
