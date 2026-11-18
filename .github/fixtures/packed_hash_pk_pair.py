"""Paired prepared-key read diagnostic where the native hash sizes differ."""

import argparse
from array import array
from contextlib import closing
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import random
from statistics import median
from time import monotonic_ns
import traceback

from two_packed_tables import hash_slots, probe


ROWS = 720_000
VERSIONS = ROWS + 65_536
SECONDS = 20
WORKERS = 2
QUERY = "SELECT payload FROM over_table WHERE id=%s"


def key(index):
    return index * 48_271 % 2_147_483_647


def connect(port):
    import psycopg

    return psycopg.connect(host="127.0.0.1", port=port, user="ramsql", dbname="ramsql",
                          sslmode="disable", autocommit=True, connect_timeout=5)


def reader(port, seed, barrier, reports):
    try:
        from psycopg.types.numeric import Int4

        rng = random.Random(seed)
        latencies = array("Q")
        misses = 0
        with closing(connect(port)) as db:
            for index in range(256):
                row = db.execute(QUERY, (Int4(key(index)),), prepare=True).fetchone()
                if row != (index % 16,):
                    raise AssertionError(f"prepared warmup mismatch at {index}")
            barrier.wait(timeout=30)
            started = monotonic_ns()
            deadline = started + 10**9 * SECONDS
            finished = started
            while finished < deadline:
                index = rng.randrange(ROWS)
                missing = rng.randrange(16) == 0
                identifier = -(index + 1) if missing else key(index)
                before = monotonic_ns()
                row = db.execute(QUERY, (Int4(identifier),), prepare=True).fetchone()
                finished = monotonic_ns()
                if row != (None if missing else (index % 16,)):
                    raise AssertionError(f"prepared read mismatch for id {identifier}: {row}")
                latencies.append(finished - before)
                misses += missing
        reports.put({"started": started, "finished": finished, "latencies": latencies, "misses": misses})
    except BaseException:
        reports.put({"error": traceback.format_exc()})


def measure(port, readers, trial):
    context = mp.get_context("spawn")
    barrier = context.Barrier(readers)
    reports = context.Queue()
    processes = []
    try:
        for index in range(readers):
            process = context.Process(target=reader, args=(port, 89_973 + trial * 101 + index, barrier, reports))
            process.start()
            processes.append(process)
        results = [reports.get(timeout=SECONDS + 60) for _ in processes]
        for result in results:
            if "error" in result:
                raise RuntimeError(result["error"])
        latencies = sorted(value for result in results for value in result["latencies"])
        if not latencies or any(not r["latencies"] or r["misses"] == 0 for r in results):
            raise AssertionError("every client must complete prepared hits and misses")
        elapsed = (max(r["finished"] for r in results) - min(r["started"] for r in results)) / 10**9
        result = {
            "seconds": elapsed, "clients": readers, "operations": len(latencies),
            "misses": sum(r["misses"] for r in results), "qps": len(latencies) / elapsed,
            "worker_operations": sorted(len(r["latencies"]) for r in results),
        }
        for percent in (50, 95, 99):
            result[f"p{percent}_us"] = latencies[(len(latencies) * percent + 99) // 100 - 1] / 1000
        return result
    finally:
        for process in processes:
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join()
        reports.close()


def server_options(binary, hash_load):
    slots = probe(binary, "--capabilities")["tables"]
    remaining = slots - 1
    options = [
        "--table-rows", ",".join(map(str, [ROWS] + [1] * remaining)),
        "--table-versions", ",".join(map(str, [VERSIONS] + [2] * remaining)),
        "--table-integral-words", ",".join(map(str, [1] + [0] * remaining)),
        "--typed-bank-memory-profile", "--work-rows", "8192", "--result-rows", "32",
        "--workers", str(WORKERS), "--max-clients", "8",
    ]
    if hash_load == 75:
        options.append("--packed-hash-load-75")
    capacity = probe(binary, "--machine-capacity", options)
    assert capacity["startup_fits"] and capacity["packed_hash_load_percent"] == hash_load
    assert capacity["storage_version_bytes"] == 32 and capacity["table_storage"][0]["rows"] == ROWS
    return options, capacity


def load(connection):
    connection.execute("CREATE TABLE over_table(id INTEGER PRIMARY KEY, payload SMALLINT NOT NULL)")
    for start in range(0, ROWS, 1000):
        data = "".join(f"{key(index)}\t{index % 16}\n" for index in range(start, start + 1000)).encode("ascii")
        with connection.cursor() as cursor, cursor.copy("COPY over_table FROM STDIN") as copy:
            copy.write(data)
    if connection.execute("SELECT COUNT(*) FROM over_table").fetchone() != (ROWS,):
        raise AssertionError("paired fixture load was incomplete")


def main():
    from test_server import Server

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    with binary.open("rb") as handle:
        binary_sha = hashlib.file_digest(handle, "sha256").hexdigest()
    result = {
        "binary_sha256": binary_sha,
        "scope": "Diagnostic of prepared PK reads at actual different hash capacities; no engine comparison",
        "query": QUERY, "rows": ROWS, "versions": VERSIONS, "duration_seconds": SECONDS,
        "key_slots": {load: hash_slots(VERSIONS, load) for load in (50, 75)},
        "row_slots": {load: hash_slots(ROWS, load) for load in (50, 75)},
        "trials": [], "passed": False,
    }
    options = {load: server_options(binary, load) for load in (50, 75)}
    assert options[50][1]["backing_bytes"] - options[75][1]["backing_bytes"] == 8_388_608
    try:
        for trial, loads in enumerate(((50, 75), (75, 50), (50, 75))):
            clients = (1, 8) if trial % 2 == 0 else (8, 1)
            for load_factor in loads:
                flags, _ = options[load_factor]
                with Server(binary, idle_ms=120_000, max_clients=8, extra_arguments=flags) as server:
                    with closing(connect(server.port)) as connection:
                        load(connection)
                    for count in clients:
                        row = measure(server.port, count, trial)
                        row.update(trial=trial + 1, hash_load_percent=load_factor)
                        result["trials"].append(row)
                        print(json.dumps(row), flush=True)
        result["medians"] = {}
        for clients in (1, 8):
            by_load = result["medians"][clients] = {}
            for load_factor in (50, 75):
                samples = [r for r in result["trials"] if r["clients"] == clients and r["hash_load_percent"] == load_factor]
                assert len(samples) == 3
                by_load[load_factor] = {metric: median(r[metric] for r in samples)
                                        for metric in ("qps", "p50_us", "p95_us", "p99_us")}
        result["passed"] = True
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
