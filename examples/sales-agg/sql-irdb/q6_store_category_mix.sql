-- IRDB dialect: string date literals instead of DATE '...'.
SELECT c.name              AS category,
       COUNT(*)            AS sales,
       SUM(s.quantity)     AS units,
       SUM(s.amount_cents) AS revenue_cents
FROM sales s
JOIN products p ON p.id = s.product_id
JOIN categories c ON c.id = p.category_id
WHERE s.store_id = :{store_id:int}
  AND s.sale_date BETWEEN '2025-03-01' AND '2025-08-31'
GROUP BY c.name
ORDER BY c.name
