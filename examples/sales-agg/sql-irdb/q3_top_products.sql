-- IRDB dialect: window functions only over an aggregate query, so the share uses SUM(SUM()) OVER ().
SELECT product_id,
       SUM(quantity)     AS units,
       SUM(amount_cents) AS revenue_cents,
       ROUND(100.0 * SUM(amount_cents) / SUM(SUM(amount_cents)) OVER (), 4) AS pct_of_total
FROM sales
GROUP BY product_id
ORDER BY revenue_cents DESC, product_id
LIMIT 10
