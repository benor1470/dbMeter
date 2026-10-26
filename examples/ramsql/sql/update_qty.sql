UPDATE items SET qty = :{$rand(1,50):int} WHERE id = :{$rand(1,500):int}
