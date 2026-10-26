-- Top 10 products by revenue with their share of the grand total.
WITH product_revenue AS (
    SELECT product_id, SUM(quantity) AS units, SUM(amount_cents) AS revenue_cents
    FROM sales
    GROUP BY product_id
)
SELECT product_id,
       units,
       revenue_cents,
       ROUND(100.0 * revenue_cents / SUM(revenue_cents) OVER (), 4) AS pct_of_total
FROM product_revenue
ORDER BY revenue_cents DESC, product_id
LIMIT 10
