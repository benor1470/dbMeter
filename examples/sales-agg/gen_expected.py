"""Recomputes the expected results of the sales-agg queries without any database.

It mirrors the integer formulas in sql/schema.sql row by row and writes expected/*.csv,
so `dbmeter check` compares the database against an independent implementation.

Usage: python -I gen_expected.py [rows]      (default 5000000, must match schema.sql)
"""
import os
import sys
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 50
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5_000_000
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expected")

REGIONS = {1: "North", 2: "South", 3: "East", 4: "West", 5: "Central"}
SEGMENTS = ["Consumer", "Corporate", "Home Office", "Small Business"]
P = 2147483647  # prime modulus of the per-column hashes
BASE = date(2025, 1, 1)
HIGH_VALUE_CENTS = 1_900_000
Q6_STORE = 17
Q6_FROM = (date(2025, 3, 1) - BASE).days
Q6_TO = (date(2025, 8, 31) - BASE).days

store_region = [None] + [REGIONS[s % 5 + 1] for s in range(1, 201)]
cat_name = [None] + ["Category %02d" % c for c in range(1, 21)]
price = [0] + [100 + (p * 7919) % 10000 for p in range(1, 1001)]
product_cat = [0] + [p % 20 + 1 for p in range(1, 1001)]
segment = [None] + [SEGMENTS[c % 4] for c in range(1, 50001)]
month = [(BASE + timedelta(days=d)).strftime("%Y-%m") for d in range(730)]


def round_half_up(value, places):
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def write(name, header, rows):
    def fmt(v):
        return "NULL" if v is None else ('"%s"' % v if "," in str(v) else str(v))

    with open(os.path.join(OUT, name), "w", newline="\n", encoding="utf-8") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(fmt(v) for v in r) + "\n")
    print("wrote expected/%s (%d rows)" % (name, len(rows)))


def main():
    os.makedirs(OUT, exist_ok=True)
    q1 = {}
    cat_count, cat_rev = [0] * 21, [0] * 21
    cat_customers = [set() for _ in range(21)]
    prod_units, prod_rev = [0] * 1001, [0] * 1001
    seg_disc = {}
    cust_count, cust_rev = [0] * 50001, [0] * 50001
    q6 = {}
    total_rev = 0

    for i in range(1, N + 1):
        pid = (i * 1103515245) % P % 1000 + 1
        sid = (i * 950706376) % P % 200 + 1
        cid = (i * 742938285) % P % 50000 + 1
        day = (i * 1583458089) % P % 730
        qty = (i * 1226874159) % P % 7 + 1
        disc = (i * 630360016) % P % 4 * 5
        amt = qty * price[pid] * (100 - disc) // 100
        total_rev += amt

        a = q1.get((store_region[sid], month[day]))
        if a is None:
            a = q1[(store_region[sid], month[day])] = [0, 0, 0]
        a[0] += 1
        a[1] += qty
        a[2] += amt

        cat = product_cat[pid]
        cat_count[cat] += 1
        cat_rev[cat] += amt
        cat_customers[cat].add(cid)

        prod_units[pid] += qty
        prod_rev[pid] += amt

        b = seg_disc.get((segment[cid], disc))
        if b is None:
            b = seg_disc[(segment[cid], disc)] = [0, 0]
        b[0] += 1
        b[1] += amt

        cust_count[cid] += 1
        cust_rev[cid] += amt

        if sid == Q6_STORE and Q6_FROM <= day <= Q6_TO:
            c = q6.get(cat)
            if c is None:
                c = q6[cat] = [0, 0, 0]
            c[0] += 1
            c[1] += qty
            c[2] += amt

    write("totals.csv", ["row_count", "revenue_cents"], [[N, total_rev]])

    write("q1_region_month.csv", ["region", "sale_month", "sales", "units", "revenue_cents"],
          [[k[0], k[1]] + v for k, v in sorted(q1.items())])

    rows = []
    for cat in sorted(range(1, 21), key=lambda c: cat_name[c]):
        rank = 1 + sum(1 for o in range(1, 21) if cat_rev[o] > cat_rev[cat])
        rows.append([cat_name[cat], cat_count[cat], len(cat_customers[cat]), cat_rev[cat], rank])
    write("q2_category_rank.csv", ["category", "sales", "customers", "revenue_cents", "revenue_rank"], rows)

    top = sorted(range(1, 1001), key=lambda p: (-prod_rev[p], p))[:10]
    write("q3_top_products.csv", ["product_id", "units", "revenue_cents", "pct_of_total"],
          [[p, prod_units[p], prod_rev[p], round_half_up(Decimal(100 * prod_rev[p]) / Decimal(total_rev), 4)]
           for p in top])

    rows = []
    grand_count = grand_rev = 0
    for seg in sorted(SEGMENTS):
        seg_count = seg_rev = 0
        for disc in sorted(d for (s, d) in seg_disc if s == seg):
            count, rev = seg_disc[(seg, disc)]
            rows.append([seg, disc, count, rev, round_half_up(Decimal(rev) / count, 2)])
            seg_count += count
            seg_rev += rev
        rows.append([seg, None, seg_count, seg_rev, round_half_up(Decimal(seg_rev) / seg_count, 2)])
        grand_count += seg_count
        grand_rev += seg_rev
    rows.append([None, None, grand_count, grand_rev, round_half_up(Decimal(grand_rev) / grand_count, 2)])
    write("q4_segment_discount_rollup.csv",
          ["segment", "discount_pct", "sales", "revenue_cents", "avg_sale_cents"], rows)

    per_seg = {}
    for c in range(1, 50001):
        if cust_count[c] and cust_rev[c] > HIGH_VALUE_CENTS:
            s = per_seg.setdefault(segment[c], [0, 0, 0, 0])
            s[0] += 1
            s[1] += cust_count[c]
            s[2] = max(s[2], cust_rev[c])
            s[3] += cust_rev[c]
    write("q5_high_value_customers.csv",
          ["segment", "customers", "sales", "max_total_cents", "avg_total_cents"],
          [[seg, v[0], v[1], v[2], round_half_up(Decimal(v[3]) / v[0], 2)] for seg, v in sorted(per_seg.items())])

    write("q6_store_category_mix.csv", ["category", "sales", "units", "revenue_cents"],
          [[cat_name[c]] + q6[c] for c in sorted(q6, key=lambda c: cat_name[c])])


if __name__ == "__main__":
    main()
