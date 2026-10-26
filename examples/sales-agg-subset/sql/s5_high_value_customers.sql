-- Two-level aggregation: totals for 50,000 customers, then high-value customers per segment
-- (../sales-agg q5 without ROUND(AVG); threshold scaled to 1M rows).
WITH customer_totals AS (
    SELECT customer_id, COUNT(*) AS sales, SUM(amount_cents) AS total_cents
    FROM sales
    GROUP BY customer_id
)
SELECT cu.segment,
       COUNT(*)            AS customers,
       SUM(ct.sales)       AS sales,
       MAX(ct.total_cents) AS max_total_cents
FROM customer_totals ct
JOIN customers cu ON cu.id = ct.customer_id
WHERE ct.total_cents > 380000
GROUP BY cu.segment
ORDER BY cu.segment
