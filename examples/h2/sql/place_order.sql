-- Two statements, so the scenario runs as one transaction and is timed as a whole.
INSERT INTO orders (customer_id, order_date, amount)
VALUES (:{$rand(1,100):int}, CURRENT_DATE, :{$rand(1,500):decimal});

INSERT INTO audit_log (thread_no, iteration, logged_at, note)
VALUES (:{$thread}, :{$iter}, :{$now}, 'order placed');
