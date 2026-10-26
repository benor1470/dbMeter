-- Per-category totals with distinct customer counts and a revenue rank (window over aggregate).
SELECT c.name                        AS category,
       COUNT(*)                      AS sales,
       COUNT(DISTINCT s.customer_id) AS customers,
       SUM(s.amount_cents)           AS revenue_cents,
       RANK() OVER (ORDER BY SUM(s.amount_cents) DESC) AS revenue_rank
FROM sales s
JOIN products p ON p.id = s.product_id
JOIN categories c ON c.id = p.category_id
GROUP BY c.name
ORDER BY c.name
