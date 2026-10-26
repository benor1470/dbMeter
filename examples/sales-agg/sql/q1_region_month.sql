-- Revenue by region and month: 3-way join + group by over all 5M rows.
SELECT r.name                          AS region,
       to_char(s.sale_date, 'YYYY-MM') AS sale_month,
       COUNT(*)                        AS sales,
       SUM(s.quantity)                 AS units,
       SUM(s.amount_cents)             AS revenue_cents
FROM sales s
JOIN stores st ON st.id = s.store_id
JOIN regions r ON r.id = st.region_id
GROUP BY r.name, to_char(s.sale_date, 'YYYY-MM')
ORDER BY region, sale_month
