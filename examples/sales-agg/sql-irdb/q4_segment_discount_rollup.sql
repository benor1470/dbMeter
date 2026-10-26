-- IRDB dialect: no ROLLUP, so the three grouping levels are combined with UNION ALL.
SELECT cu.segment, s.discount_pct, COUNT(*) AS sales, SUM(s.amount_cents) AS revenue_cents,
       ROUND(AVG(s.amount_cents), 2) AS avg_sale_cents
FROM sales s
JOIN customers cu ON cu.id = s.customer_id
GROUP BY cu.segment, s.discount_pct
UNION ALL
SELECT cu.segment, NULL, COUNT(*), SUM(s.amount_cents), ROUND(AVG(s.amount_cents), 2)
FROM sales s
JOIN customers cu ON cu.id = s.customer_id
GROUP BY cu.segment
UNION ALL
SELECT NULL, NULL, COUNT(*), SUM(amount_cents), ROUND(AVG(amount_cents), 2)
FROM sales
