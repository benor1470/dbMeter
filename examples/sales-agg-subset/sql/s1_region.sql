-- Revenue by region: 3-way join + group by over all sales rows (../sales-agg q1 without to_char months).
SELECT r.name              AS region,
       COUNT(*)            AS sales,
       SUM(s.quantity)     AS units,
       SUM(s.amount_cents) AS revenue_cents
FROM sales s
JOIN stores st ON st.id = s.store_id
JOIN regions r ON r.id = st.region_id
GROUP BY r.name
ORDER BY region
