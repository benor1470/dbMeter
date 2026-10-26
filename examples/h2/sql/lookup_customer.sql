-- :{customer_id:int} is bound from the customer_id column of the scenario's params file.
SELECT c.id, c.name, COUNT(o.id) AS orders, COALESCE(SUM(o.amount), 0) AS total
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.id
WHERE c.id = :{customer_id:int}
GROUP BY c.id, c.name;
