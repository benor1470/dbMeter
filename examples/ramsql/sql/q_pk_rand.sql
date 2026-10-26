SELECT id, region, price, qty, name FROM items WHERE id = :{$rand(1,500):int}
