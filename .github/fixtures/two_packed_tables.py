"""Native PostgreSQL wire correctness and restart for two one-word RAMSQL banks."""

import argparse
from contextlib import closing
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from time import monotonic


FULL_ROWS = 100_000_000
SMOKE_ROWS = 262_144
VERSIONS_HEADROOM = 65_536
BATCH_ROWS = 1_000
CLIENTS = 4
WORKERS = 2
TABLES = ("fact_left", "fact_right")
JOIN = " FROM fact_left a JOIN fact_right b ON a.id=b.id"


def exact(label, got, want):
    if got != want:
        raise AssertionError(f"{label}: got {got!r}, want {want!r}")


def probe(binary, command, flags=()):
    return json.loads(subprocess.check_output([str(binary), command, *flags], timeout=30))


def power_of_two_at_least(value):
    return 1 << (value - 1).bit_length()


def bank_bytes(rows, versions, words):
    return (16 * rows + (32 + 8 * words) * versions
            + 4 * power_of_two_at_least(2 * rows) + 4 * power_of_two_at_least(2 * versions))


def plan(binary, rows, wal, memory_budget):
    slots = probe(binary, "--capabilities")["tables"]
    if slots < 2:
        raise AssertionError("fixture requires two independently admitted banks")
    spare = slots - 2
    versions = rows + VERSIONS_HEADROOM
    options = [
        "--table-rows", ",".join(map(str, [rows] * 2 + [1] * spare)),
        "--table-versions", ",".join(map(str, [versions] * 2 + [2] * spare)),
        "--table-integral-words", ",".join(map(str, [1, 1] + [0] * spare)),
        "--typed-bank-memory-profile", "--work-rows", "8192", "--result-rows", "1024",
        "--workers", str(WORKERS), "--max-clients", str(CLIENTS),
    ]
    if memory_budget is not None:
        options.extend(("--memory-budget", str(memory_budget)))
    machine = probe(binary, "--machine-capacity", options)
    capacities = probe(binary, "--capabilities", options)
    exact("external MVCC header width", machine["storage_version_bytes"], 32)
    exact("planned worker count", machine["read_workers"], WORKERS)
    banks = machine["table_storage"]
    expected = dict(rows=rows, versions=versions, integral_row_words=1,
                    committed_scan_projection=False, secondary_hashes=0)
    exact("two packed fact banks", banks[:2], [expected, expected])
    minimum = 2 * bank_bytes(rows, rows + 1, 1)
    requested = 2 * bank_bytes(rows, versions, 1)
    if machine["backing_bytes"] <= requested:
        raise AssertionError("engine backing estimate omits the other slots or query references")

    page = os.sysconf("SC_PAGE_SIZE")

    def rounded(value):
        return ((value + page - 1) // page) * page

    read_regions = WORKERS * (rounded(machine["read_context_payload_bytes_per_worker"]) + page)
    client_regions = CLIENTS * (rounded(capacities["result_bytes_per_client"])
                                + rounded(capacities["output_bytes_per_client"]))
    peak_regions = machine["startup_region_bytes"] + read_regions + client_regions
    grant = machine["new_region_bytes"]
    fits = machine["startup_fits"] and peak_regions <= grant

    # Logical row op, OID, RowID and column count take 14 bytes. Each
    # non-NULL integer cell takes one tag and eight value bytes.
    row_records = 2 * rows * 32
    copy_frames = 2 * ((rows + BATCH_ROWS - 1) // BATCH_ROWS)
    wal_floor = row_records + copy_frames * (8 + 48)
    wal_room = wal_floor + 64 * 1024**2
    free = shutil.disk_usage(wal.parent).free
    return options, {
        "machine_capacity": machine,
        "startup_options": options,
        "minimum_two_bank_bytes": minimum,
        "requested_two_bank_bytes": requested,
        "six_word_two_bank_bytes": 2 * bank_bytes(rows, versions, 6),
        "peak_client_regions_bytes": client_regions,
        "peak_reader_regions_bytes": read_regions,
        "peak_server_regions_bytes": peak_regions,
        "region_shortfall_bytes": max(0, peak_regions - grant),
        "memory_admitted": fits,
        "available_wal_disk_bytes": free,
        "one_wal_floor_bytes": wal_floor,
        "two_independent_wals_floor_bytes": 2 * wal_floor,
        "required_wal_disk_bytes": wal_room,
        "disk_admitted": free >= wal_room,
    }


def value(table_index, key):
    return ((3 * key + 5) if table_index else key) % 16


def check_queries(connection, rows):
    cardinalities = []
    for name in TABLES:
        bounds = connection.execute(f"SELECT COUNT(*),MIN(id),MAX(id) FROM {name}").fetchone()
        exact(f"{name} cardinality", bounds, (rows, 0, rows - 1))
        cardinalities.append(bounds)
    base, tail = divmod(rows, 16)
    expected_groups = []
    for bucket in range(16):
        count = base + int(bucket < tail)
        if count:
            expected_groups.append((bucket, count, bucket * count, value(1, bucket) * count))
    groups = connection.execute(
        "SELECT a.payload,COUNT(*),SUM(a.payload),SUM(b.payload)" + JOIN
        + " GROUP BY a.payload ORDER BY a.payload"
    ).fetchall()
    exact("whole key join and sums by bucket", groups, expected_groups)
    start = rows - 128
    keys = list(range(start, rows))
    expected_range = sorted(((key, value(0, key), value(1, key)) for key in keys),
                            key=lambda row: (-row[2], row[0]))[:20]
    ranged = connection.execute(
        "SELECT a.id,a.payload,b.payload" + JOIN
        + f" WHERE a.id>={start} ORDER BY b.payload DESC,a.id LIMIT 20"
    ).fetchall()
    exact("bounded ordered range across both banks", ranged, expected_range)
    return {"table_bounds": cardinalities, "whole_join_groups": groups, "ordered_range": ranged}


def load_and_reopen(binary, wal, rows, options, report):
    import psycopg
    from benchmark import process_memory
    from test_persistence import DurableServer

    if wal.exists():
        raise FileExistsError(wal)
    options = (*options, "--idle-ms", "120000")

    def connection_to(server):
        return partial(psycopg.connect, host="127.0.0.1", port=server.port, user="ramsql",
                       dbname="ramsql", sslmode="disable", autocommit=True,
                       connect_timeout=5, prepare_threshold=None)

    with DurableServer(binary, wal, create=True, extra_arguments=options) as server:
        connect = connection_to(server)
        report["stage"] = "durable_copy"
        report["loaded_rows"] = dict.fromkeys(TABLES, 0)
        with closing(connect()) as db:
            for name in TABLES:
                db.execute(f"CREATE TABLE {name}(id INTEGER PRIMARY KEY, payload SMALLINT NOT NULL)")
            for index, name in enumerate(TABLES):
                for start in range(0, rows, BATCH_ROWS):
                    stop = min(start + BATCH_ROWS, rows)
                    block = "".join(f"{key}\t{value(index, key)}\n" for key in range(start, stop)).encode("ascii")
                    with db.cursor() as cursor, cursor.copy(f"COPY {name} FROM STDIN") as copy:
                        copy.write(block)
                    report["loaded_rows"][name] = stop
                    if stop % 1_000_000 == 0 or stop == rows:
                        print(f"committed {name}: {stop} rows", flush=True)
            report["stage"] = "verify_before_crash"
            report["before_recovery"] = check_queries(db, rows)
            report["memory_before_recovery"] = process_memory(server.process)

        report["stage"] = "crash_with_private_update"
        with closing(connect()) as writer, closing(connect()) as observer:
            writer.execute("BEGIN")
            update = writer.execute(f"UPDATE fact_left SET payload=-1 WHERE id={rows - 1}")
            exact("private update count", update.rowcount, 1)
            sql = f"SELECT payload FROM fact_left WHERE id={rows - 1}"
            exact("private value", writer.execute(sql).fetchone(), (-1,))
            exact("observed committed value", observer.execute(sql).fetchone(), (value(0, rows - 1),))
            server.crash()
            report["first_process_exited"] = server.process.poll() is not None
    report["wal_bytes"] = wal.stat().st_size
    report["stage"] = "recover_original_wal"
    started = monotonic()
    with DurableServer(binary, wal, extra_arguments=options, startup_timeout=2400) as server:
        report["recovery_seconds"] = monotonic() - started
        with closing(connection_to(server)()) as db:
            report["after_recovery"] = check_queries(db, rows)
            sql = f"SELECT payload FROM fact_left WHERE id={rows - 1}"
            exact("private update absent after crash", db.execute(sql).fetchone(), (value(0, rows - 1),))
        report["memory_after_recovery"] = process_memory(server.process)
    exact("durable result identity", report["after_recovery"], report["before_recovery"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--wal", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--memory-budget", type=int)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke and not args.execute:
        parser.error("--smoke requires --execute")
    if args.memory_budget is not None and args.memory_budget <= 0:
        parser.error("the memory budget must be positive")
    binary = args.binary.resolve(strict=True)
    wal = args.wal.resolve()
    rows = SMOKE_ROWS if args.smoke else FULL_ROWS
    report = {
        "suite": "dual_packed_tables_native",
        "rows_per_table": rows,
        "loaded_rows": dict.fromkeys(TABLES, 0),
        "stress_passed": False,
        "mode": "smoke" if args.smoke else "full" if args.execute else "preflight",
        "stage": "capacity",
        "scope": "Two tables with INTEGER primary key and non-null SMALLINT; synchronous WAL and process restart",
    }
    try:
        with binary.open("rb") as handle:
            report["binary_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
        options, capacity = plan(binary, rows, wal, args.memory_budget)
        report.update(capacity)
        if not args.execute:
            report["stage"] = "preflight_only"
        else:
            if not capacity["memory_admitted"] or not capacity["disk_admitted"]:
                raise RuntimeError("runner cannot admit the exact workload; no database was opened")
            load_and_reopen(binary, wal, rows, options, report)
            report.update(stage="complete", stress_passed=True)
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['mode']}: {report['stage']} ({rows} rows per table)", flush=True)


if __name__ == "__main__":
    main()
