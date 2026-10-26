-- Totals per customer segment and discount (../sales-agg q4 without ROLLUP subtotals and ROUND(AVG)).
SELECT cu.segment,
       s.discount_pct,
       COUNT(*)            AS sales,
       SUM(s.amount_cents) AS revenue_cents
FROM sales s
JOIN customers cu ON cu.id = s.customer_id
GROUP BY cu.segment, s.discount_pct
ORDER BY cu.segment, s.discount_pct
