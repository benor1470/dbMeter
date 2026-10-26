-- Subtotals per segment and discount, per segment, and a grand total (ROLLUP).
SELECT cu.segment,
       s.discount_pct,
       COUNT(*)                     AS sales,
       SUM(s.amount_cents)          AS revenue_cents,
       ROUND(AVG(s.amount_cents), 2) AS avg_sale_cents
FROM sales s
JOIN customers cu ON cu.id = s.customer_id
GROUP BY ROLLUP (cu.segment, s.discount_pct)
ORDER BY cu.segment NULLS LAST, s.discount_pct NULLS LAST
