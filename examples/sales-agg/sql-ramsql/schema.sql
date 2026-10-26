-- RAMSQL can't hold the 5M-row dataset (default profile: 512 rows per table, 8 tables, 8 columns;
-- single-row INSERT only; no DATE type), so this builds the same tables with a few rows. It is
-- enough to show which of the aggregation queries RAMSQL can execute.
DROP TABLE sales;
DROP TABLE customers;
DROP TABLE products;
DROP TABLE categories;
DROP TABLE stores;
DROP TABLE regions;

CREATE TABLE regions (id INT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE stores (id INT PRIMARY KEY, region_id INT NOT NULL, name TEXT NOT NULL);
CREATE TABLE categories (id INT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE products (id INT PRIMARY KEY, category_id INT NOT NULL, unit_price_cents INT NOT NULL);
CREATE TABLE customers (id INT PRIMARY KEY, segment TEXT NOT NULL);
CREATE TABLE sales (id BIGINT PRIMARY KEY, product_id INT NOT NULL, store_id INT NOT NULL, customer_id INT NOT NULL,
                    sale_date TEXT NOT NULL, quantity INT NOT NULL, discount_pct INT NOT NULL, amount_cents BIGINT NOT NULL);

INSERT INTO regions (id, name) VALUES (1, 'North');
INSERT INTO regions (id, name) VALUES (2, 'South');
INSERT INTO stores (id, region_id, name) VALUES (17, 1, 'Store 17');
INSERT INTO stores (id, region_id, name) VALUES (18, 2, 'Store 18');
INSERT INTO categories (id, name) VALUES (1, 'Category 01');
INSERT INTO categories (id, name) VALUES (2, 'Category 02');
INSERT INTO products (id, category_id, unit_price_cents) VALUES (1, 1, 1000);
INSERT INTO products (id, category_id, unit_price_cents) VALUES (2, 2, 2500);
INSERT INTO customers (id, segment) VALUES (1, 'Consumer');
INSERT INTO customers (id, segment) VALUES (2, 'Corporate');
INSERT INTO sales (id, product_id, store_id, customer_id, sale_date, quantity, discount_pct, amount_cents) VALUES (1, 1, 17, 1, '2025-03-05', 2, 0, 2000);
INSERT INTO sales (id, product_id, store_id, customer_id, sale_date, quantity, discount_pct, amount_cents) VALUES (2, 2, 18, 2, '2025-04-11', 1, 5, 2375);
INSERT INTO sales (id, product_id, store_id, customer_id, sale_date, quantity, discount_pct, amount_cents) VALUES (3, 1, 17, 2, '2025-07-20', 3, 10, 2700);
