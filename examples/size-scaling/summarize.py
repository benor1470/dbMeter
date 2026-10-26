"""Collects the size-scaling outputs (JUnit checks, logs, run reports) into results.json.

Usage: python -I summarize.py <reports/size-scaling dir>
"""
import csv
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

DBS = ["pg", "irdb", "ramsql"]
SIZES = [100, 512, 1000, 10_000, 100_000, 1_000_000]
KINDS = ["pk", "filter", "topn", "fetch_all"]


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def checks(out, db):
    path = os.path.join(out, "checks-%s.xml" % db)
    result = {}
    if not os.path.exists(path):
        return result
    for case in ET.parse(path).getroot().iter("testcase"):
        failure = case.find("failure")
        result[case.get("name")] = {
            "pass": failure is None,
            "ms": round(float(case.get("time", 0)) * 1000, 2),
            "message": None if failure is None else (failure.text or failure.get("message") or "").strip(),
        }
    return result


def run_report(out, run):
    d = os.path.join(out, "runs", run)
    if not os.path.isdir(d):
        return None
    for name in os.listdir(d):
        if name.endswith(".json"):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                return json.load(f)
    return None


def percentile(hist, p):
    """Same log-linear bucket math as dbmeter's Histogram (subBits=5), microseconds -> ms."""
    count = hist["count"]
    if count == 0:
        return None
    sub = 1 << hist["subBits"]
    rank = max(1, -(-p * count // 100))
    seen = 0
    for idx in sorted(int(k) for k in hist["buckets"]):
        seen += hist["buckets"][str(idx)]
        if seen >= rank:
            if idx < sub:
                lo = hi = idx
            else:
                shift = idx // sub - 1
                lo = (idx % sub + sub) << shift
                hi = ((idx % sub + sub + 1) << shift) - 1
            return round(max(hist["min"], min(hist["max"], (lo + hi) // 2)) / 1000, 3)
    return round(hist["max"] / 1000, 3)


def perf(report):
    window = max(0.001, (report["measuredEnd"] - report["measuredStart"]) / 1000)
    res = {}
    for name, s in report["scenarios"].items():
        kind = name.split("_", 1)[1]
        h = s["latencyMicros"]
        res[kind] = {
            "count": s["count"], "errors": s["errors"], "rows": s["rows"],
            "tps": round(s["count"] / window, 2),
            "avg_ms": round(h["sum"] / h["count"] / 1000, 3) if h["count"] else None,
            "p50_ms": percentile(h, 50), "p99_ms": percentile(h, 99),
            "max_ms": round(h["max"] / 1000, 3) if h["count"] else None,
            "first_error": next(iter(s["errorMessages"]), None),
        }
    return res


def ramsql_load(out, n):
    path = os.path.join(out, "ramsql-load-%d.txt" % n)
    if not os.path.exists(path):
        return None
    text = read(path)
    m = re.search(r"check\.setup: (\d+) statement\(s\)(?:, (\d+) failed)? in ([\d.]+) ms", text)
    warnings = re.findall(r"warning: check\.setup statement (\d+) failed: (.*)", text)
    # Statement 1 is DROP TABLE (fails on a fresh server), 2 is CREATE TABLE, then one INSERT per row.
    insert_failures = [(int(k), msg) for k, msg in warnings if int(k) > 2]
    failed = int(m.group(2) or 0) if m else None
    drop_failed = any(int(k) == 1 for k, _ in warnings)
    rejected = None if failed is None else failed - (1 if drop_failed else 0)
    return {
        "rows_attempted": n,
        "rows_rejected": rejected,
        "rows_stored": None if rejected is None else n - rejected,
        "load_ms": float(m.group(3)) if m else None,
        "first_rejected_row": insert_failures[0][0] - 2 if insert_failures else None,
        "first_error": insert_failures[0][1].strip() if insert_failures else None,
    }


def conn32(out, db):
    report = run_report(out, "conn32-%s" % db)
    log = read(os.path.join(out, "conn32-%s.txt" % db)) if os.path.exists(os.path.join(out, "conn32-%s.txt" % db)) else ""
    err = re.search(r"^error: (.*)$", log, re.M)
    if report is None:
        return {"ok": False, "error": err.group(1).strip() if err else "no report"}
    p = perf(report).get("pk", {})
    return {"ok": p.get("errors", 1) == 0 and p.get("count", 0) > 0, "tps": p.get("tps"), "p99_ms": p.get("p99_ms"),
            "errors": p.get("errors"), "error": p.get("first_error")}


def main():
    out = sys.argv[1]
    results = {"sizes": SIZES, "kinds": KINDS, "databases": {}, "environment": None,
               "checks": {db: checks(out, db) for db in DBS}, "perf": {}, "ramsql_load": {}, "conn32": {}}
    for n in SIZES:
        results["perf"][str(n)] = {}
        for db in DBS:
            r = run_report(out, "size-%s-%d" % (db, n))
            if r is None:
                continue
            results["perf"][str(n)][db] = perf(r)
            dbinfo = r["database"]
            results["databases"].setdefault(db, (dbinfo.get("label") or "%s %s" % (dbinfo["product"], dbinfo["productVersion"])))
            if results["environment"] is None:
                node = r["nodes"][0]
                results["environment"] = {k: node.get(k) for k in ("host", "cpus", "os", "java", "settings")}
        results["ramsql_load"][str(n)] = ramsql_load(out, n)
    for db in DBS:
        results["conn32"][db] = conn32(out, db)
    copy = os.path.join(out, "copy-irdb.txt")
    results["irdb_copy"] = read(copy).strip().splitlines()[-1] if os.path.exists(copy) else None
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print("wrote", os.path.join(out, "results.json"))


if __name__ == "__main__":
    main()
