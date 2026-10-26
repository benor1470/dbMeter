SELECT c.id, c.name, COUNT(o.id) AS orders, customer_total(c.id) AS total
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.id
WHERE c.id = :{customer_id:int}
GROUP BY c.id, c.name;
