-- Deterministic sales star schema: 5,000,000 sales rows plus dimensions.
-- Every value is an integer formula of the row number, so gen_expected.py can recompute
-- all aggregates independently of the database. Keep the formulas in sync with that script.
DROP SCHEMA IF EXISTS dbmeter_agg CASCADE;
CREATE SCHEMA dbmeter_agg;
SET search_path TO dbmeter_agg;

CREATE UNLOGGED TABLE regions (id INT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO regions VALUES (1, 'North'), (2, 'South'), (3, 'East'), (4, 'West'), (5, 'Central');

CREATE UNLOGGED TABLE stores (id INT PRIMARY KEY, region_id INT NOT NULL, name TEXT NOT NULL);
INSERT INTO stores SELECT s, s % 5 + 1, 'Store ' || s FROM generate_series(1, 200) s;

CREATE UNLOGGED TABLE categories (id INT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO categories SELECT c, 'Category ' || lpad(c::text, 2, '0') FROM generate_series(1, 20) c;

CREATE UNLOGGED TABLE products (id INT PRIMARY KEY, category_id INT NOT NULL, unit_price_cents INT NOT NULL);
INSERT INTO products SELECT p, p % 20 + 1, 100 + (p * 7919) % 10000 FROM generate_series(1, 1000) p;

CREATE UNLOGGED TABLE customers (id INT PRIMARY KEY, segment TEXT NOT NULL);
INSERT INTO customers
SELECT c, (ARRAY['Consumer', 'Corporate', 'Home Office', 'Small Business'])[c % 4 + 1]
FROM generate_series(1, 50000) c;

CREATE UNLOGGED TABLE sales (
    id           BIGINT NOT NULL,
    product_id   INT    NOT NULL,
    store_id     INT    NOT NULL,
    customer_id  INT    NOT NULL,
    sale_date    DATE   NOT NULL,
    quantity     INT    NOT NULL,
    discount_pct INT    NOT NULL,
    amount_cents BIGINT NOT NULL
);

-- Each column uses its own multiplicative hash modulo the prime 2^31-1, so columns are uncorrelated.
INSERT INTO sales
SELECT g.i, g.pid, g.sid, g.cid,
       DATE '2025-01-01' + g.day::int,
       g.qty, g.disc,
       g.qty * p.unit_price_cents * (100 - g.disc) / 100
FROM (
    SELECT i,
           (i * 1103515245) % 2147483647 % 1000 + 1  AS pid,
           (i * 950706376) % 2147483647 % 200 + 1    AS sid,
           (i * 742938285) % 2147483647 % 50000 + 1  AS cid,
           (i * 1583458089) % 2147483647 % 730       AS day,
           (i * 1226874159) % 2147483647 % 7 + 1     AS qty,
           (i * 630360016) % 2147483647 % 4 * 5      AS disc
    FROM generate_series(1::bigint, 5000000) i
) g
JOIN products p ON p.id = g.pid;

ALTER TABLE sales ADD PRIMARY KEY (id);
VACUUM ANALYZE;
