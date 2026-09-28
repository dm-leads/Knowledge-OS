MODEL (
  name metrics.deals_by_month,
  kind FULL,
  grain (brand, month),
  audits (
    NOT_NULL(columns = (brand, month)),
    UNIQUE_COMBINATION_OF_COLUMNS(columns = (brand, month)),
    brand_not_blank
  )
);

SELECT
  brand,
  CAST(DATE_TRUNC('month', created_at) AS DATE) AS month,
  COUNT(*) AS deals
FROM facts.deal
WHERE deleted_at IS NULL
GROUP BY brand, CAST(DATE_TRUNC('month', created_at) AS DATE)
