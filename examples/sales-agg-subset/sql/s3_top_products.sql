-- Top 10 products by revenue (../sales-agg q3 without the share-of-total window).
WITH product_revenue AS (
    SELECT product_id, SUM(quantity) AS units, SUM(amount_cents) AS revenue_cents
    FROM sales
    GROUP BY product_id
)
SELECT product_id, units, revenue_cents
FROM product_revenue
ORDER BY revenue_cents DESC, product_id
LIMIT 10
