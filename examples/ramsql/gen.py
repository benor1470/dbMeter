"""Generates the RAMSQL example: sql/schema.sql (500 single-row INSERTs, since RAMSQL has no
INSERT ... SELECT), expected/*.csv computed here independently of any database, and data/*.csv.

RAMSQL 0.3.0-alpha.2 limits (default profile): 8 tables, 8 columns, 512 rows per table,
no joins/aggregates/expressions, one ORDER BY column. The workload stays inside them.

Usage: python -I gen.py
"""
import os

ROWS = 500
HERE = os.path.dirname(os.path.abspath(__file__))
PK_ID = 123
SCAN_REGION, SCAN_MIN_PRICE = 3, 5000


def item(i):
    return {
        "id": i,
        "region": (i * 7919) % 5 + 1,
        "price": 100 + (i * 104729) % 10000,
        "qty": (i * 31) % 50 + 1,
        "name": "item-%03d" % i,
    }


def write(rel, lines):
    path = os.path.join(HERE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", rel)


def main():
    items = [item(i) for i in range(1, ROWS + 1)]
    write("sql/schema.sql",
          ["DROP TABLE items;",
           "CREATE TABLE items (id INT PRIMARY KEY, region INT NOT NULL, price INT NOT NULL, "
           "qty INT NOT NULL, name TEXT NOT NULL);"]
          + ["INSERT INTO items (id, region, price, qty, name) VALUES (%d, %d, %d, %d, '%s');"
             % (r["id"], r["region"], r["price"], r["qty"], r["name"]) for r in items])

    r = items[PK_ID - 1]
    write("expected/q_pk.csv", ["id,region,price,qty,name",
                                "%d,%d,%d,%d,%s" % (r["id"], r["region"], r["price"], r["qty"], r["name"])])

    hits = sorted((x for x in items if x["region"] == SCAN_REGION and x["price"] > SCAN_MIN_PRICE),
                  key=lambda x: -x["price"])[:10]
    write("expected/q_scan.csv", ["id,price"] + ["%d,%d" % (x["id"], x["price"]) for x in hits])

    write("data/scan_params.csv", ["region,min_price"]
          + ["%d,%d" % (reg, p) for reg in range(1, 6) for p in (1000, 5000, 9000)])


if __name__ == "__main__":
    main()
