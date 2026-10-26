WITH region_totals AS (
    SELECT c.region,
           COUNT(*)      AS orders,
           SUM(o.amount) AS revenue
    FROM orders o
    JOIN customers c ON c.id = o.customer_id
    WHERE o.order_date >= DATE '2026-01-01'
    GROUP BY c.region
)
SELECT region,
       orders,
       revenue,
       RANK() OVER (ORDER BY revenue DESC) AS revenue_rank
FROM region_totals
ORDER BY region;
