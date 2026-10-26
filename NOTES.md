# Session notes — 2026-10-26 (RAMSQL 0.3.0-beta.1 re-test)

- RAMSQL tested: **0.3.0-beta.1**, built from branch `release/beta-usability` (52da327) with Sig 0.6.4
  (`tools/sig/`): `sig.exe build -Doptimize=ReleaseFast` in `tools/ramsql` (clone now on local branch `beta-usability`).
  Binaries: `tools/ramsql-bin/beta1/ramsql.exe` (stock profile), `tools/ramsql-bin/beta1-w100k/ramsql.exe`
  (`-Dwork-rows=100000 -Dresult-rows=4096`), `tools/ramsql-bin/a4/...` (published alpha.4).
  `-Dwork-rows=1000000` builds a 2.3 GB .exe that Windows won't load (workspace is static image data).
- Start: `ramsql.exe --memory --insecure-local --port 5433 --rows 1000000` (commits ~19 GB: ~19 KB per row slot,
  every table gets the full budget). `ramsql.exe --capabilities --rows N` prints all budgets.
- Load limits: <=512 expression nodes per statement (~100 rows x 4 values, ~50 x 8) and 16 KB per query packet
  (bigger closes the connection). gen.py scripts now emit batched multi-row INSERTs (100 / 50 rows).
- `examples/size-scaling/run_all.ps1` now takes `-Databases pg,ramsql -OutName <dir>` and has an isolated
  per-query phase. Results: `reports/size-scaling-beta1/` (iso-*.txt = isolated runs).
- New `examples/sales-agg-subset/` (1M sales rows, SQL subset both DBs run; `gen.py` computes expected results).
  Results: `reports/sales-subset/`. PG passes 7/7; RAMSQL stock 1/7, work-rows-100k build 3/7.
- `reports/ab-versions/`: alpha.2 vs alpha.4 vs beta.1 on the 512-row table, same machine.

Key findings:
- Correct everywhere results fit; no silent truncation any more (beyond-budget -> 54000 errors).
- PK lookup regression: 8 threads alpha.2 84k/s -> alpha.4 52k/s -> beta.1 35k/s (PG 77k). 1 thread:
  alpha.2 26k, beta.1 19.5k, PG 15.3k. beta.1 peaks ~50k at 4 clients and drops at 8.
- top-N much faster (512 rows: 2.7k -> 21k/s); 1M-row top-N ~= PG (21 vs 20 q/s). Filter scans 3-9x slower than PG
  under 8 clients (single reactor). Results > 512 rows fail in the stock build (fetch_all >= 1000, 1M filter).
- 3-way joins materialize the unfiltered intermediate join -> WorkspaceCapacity on 1M rows even with selective WHERE.
- s3/s5 (CTE + GROUP BY): 1 client RAMSQL 130/190 ms vs PG 95/405 ms; 8 clients PG 3-4x more throughput.
- >16 connections still get a TCP reset (no 53300 error). No to_char/date_trunc/extract/ROUND, windows, ROLLUP.

# Session notes — 2026-10-23

Handoff for the next session. README.md has the tool reference; this file has the state and what's next.

## State

- **dbMeter** (Java 11, no deps) works: `info`, `check`, `load`, `merge`, `compare`. Build: `./build.ps1`
  → `build/dbmeter.jar` (+ `build/irdb-jdbc.jar` when `drivers/jirdb-*.jar` is present).
- Git repo: https://github.com/benor1470/dbMeter (private, branch `main`). `.gitignore` covers `build/`, `reports/`, `drivers/*`, `tools/`.
- Servers were **shut down** at the end of the session (PostgreSQL service left running, it was already running).

## Databases and how to start them

| DB | Where | Start | Connect |
|---|---|---|---|
| PostgreSQL 18.3 | Windows service `postgresql-x64-18` | already running | localhost:5432, user `postgres`, password `1` (configs default to it via `${env:PGPASSWORD:-1}`) |
| InMemory.Net IRDB | `tools/irdb/irdb_files/irdb_core10/` (.NET 10 build) | `irdbServerConsole.exe irdb.ini` from that dir | port 5060, user `default`, password `!TopSecret`; `irdb.ini` has `nocache=true` (subquery cache inflated q5 ~10×) |
| RAMSQL 0.3.0-alpha.2 | `tools/ramsql-bin/x64/ramsql-0.3.0-alpha.2-x86_64-windows/ramsql.exe` | `ramsql.exe --memory --insecure-local --port 5433` | `jdbc:postgresql://127.0.0.1:5433/ramsql`, user `postgres`, empty password, `sslmode=disable` |

- RAMSQL source clone: `tools/ramsql` (github.com/SB0LTD/ramsql, accessed with the user's git credentials; `gh` CLI not installed).
- IRDB data saved in `tools/irdb/data` (databases `sales_agg`, `size_bench`; reload automatically on connect).
- RAMSQL is in-memory: data is gone after shutdown. Reload with the example's setup / `run_all.ps1`.

## IRDB JDBC adapter (`irdb-jdbc/`)

Wraps the vendor's Java client `drivers/jirdb-2025-02-02.jar` (needs `drivers/fastutil-8.5.15.jar`).
URL `jdbc:irdb://host:5060/<db>`. `dbmeter.irdb.CopyToIrdb` copies tables from any JDBC source into IRDB.
IRDB quirks: no SQL comments (adapter strips them), prepare fails without `?` params (adapter runs those as plain
queries), no ROLLUP / to_char / DATE literals / NULLS LAST / windows over CTE columns, `rows` reserved,
alias `month` comes back as `m`.

## Examples and results (`reports/` is git-ignored but kept on disk)

- `examples/sales-agg` — 5M-row aggregation suite, independent expected results (`gen_expected.py`).
  PostgreSQL: run `check` first (builds data). IRDB: copy from PG with CopyToIrdb (command in `irdb.properties`).
  Result: PG 2.5 q/s, IRDB 170 q/s, RAMSQL 0 (no joins/aggregates). `reports/compare-agg/`.
- `examples/ramsql` — point-query workload in RAMSQL's SQL subset; `reports/compare-point/`.
- `examples/size-scaling` — table sizes 100..1M on all three DBs. `gen.py` generates configs/data/expected,
  `run_all.ps1` runs everything (needs a FRESH RAMSQL), `summarize.py` → `reports/size-scaling/results.json`.
  Isolated 512-row runs (one query at a time) were run by hand: `reports/size-scaling/runs/iso-*`.

## Key findings (RAMSQL)

- Stores max 512 rows/table (row 513 → `RowCapacity` 54000); queries then silently answer from 512 rows.
- At 512 rows: PK lookup ≈ PostgreSQL (119k vs 126k ops/s), top-N sort 10× slower (7k vs 68k), mixed 19k vs 63k
  (single reactor thread: one sort blocks all connections).
- Fails: 200-byte TEXT, 9 columns, >8 tables, 32 connections (socket abort), `isValid()` false (empty-query ping →
  Syntax error), `SHOW TRANSACTION ISOLATION LEVEL` unsupported.
- Report for the RAMSQL developer (Claude Docs, private until shared):
  https://claude.ai/artifact/AWgGhdYzxmopCmaWHtwbhc — "RAMSQL Table-Size Scaling Report".

## Possible next steps

- Share/send the report; optionally open a GitHub issue on SB0LTD/ramsql (ask the user first).
- Add the isolated per-query 512-row runs to `run_all.ps1` so the report's chart is reproducible in one command.
- Re-test when RAMSQL ships aggregates or a larger row capacity (rerun `examples/sales-agg` + `size-scaling`).

- dbMeter ideas not done: publish metrics to a DB table for multi-machine runs (today: shared folder + `merge`),
  CSV-driven data load command for RAMSQL (today: generated single-row INSERT scripts).
