SELECT id, price FROM items
WHERE region = :{region:int} AND price > :{min_price:int}
ORDER BY price DESC
LIMIT 10
