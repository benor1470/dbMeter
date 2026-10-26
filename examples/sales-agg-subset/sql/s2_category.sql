-- Per-category totals with distinct customer counts (../sales-agg q2 without the RANK() window).
SELECT c.name                        AS category,
       COUNT(*)                      AS sales,
       COUNT(DISTINCT s.customer_id) AS customers,
       SUM(s.amount_cents)           AS revenue_cents
FROM sales s
JOIN products p ON p.id = s.product_id
JOIN categories c ON c.id = p.category_id
GROUP BY c.name
ORDER BY c.name
