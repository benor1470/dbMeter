-- IRDB dialect: FORMAT(date, 'yyyy-MM') instead of PostgreSQL to_char.
SELECT r.name                            AS region,
       FORMAT(s.sale_date, 'yyyy-MM')    AS sale_month,
       COUNT(*)                          AS sales,
       SUM(s.quantity)                   AS units,
       SUM(s.amount_cents)               AS revenue_cents
FROM sales s
JOIN stores st ON st.id = s.store_id
JOIN regions r ON r.id = st.region_id
GROUP BY r.name, FORMAT(s.sale_date, 'yyyy-MM')
ORDER BY region, sale_month
