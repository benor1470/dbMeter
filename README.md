# dbMeter

A command-line tool that tests any SQL database you can reach over JDBC. You supply the vendor's driver jar and the connection properties. It has no dependencies beyond a Java 11+ JDK.

- **`load`**: runs weighted SQL scenarios on N concurrent connections. It reports throughput, latency percentiles, errors and a per-second timeline.
- **`check`**: correctness tests. It runs SQL and compares the result with an expected value, row count, CSV result set, update count or error, plus an optional time limit.
- **`merge`**: combines the reports from several machines that load the same database into one result. Percentiles stay exact because histograms are merged, not averages.
- **`info`**: checks connectivity and shows driver and database metadata.

## Build & run

```
./build.ps1            # or ./build.sh   -> build/dbmeter.jar
dbmeter info  -c examples/h2/h2.properties
dbmeter check -c examples/h2/h2.properties --junit build/junit.xml
dbmeter load  -c examples/h2/h2.properties
```

Use `dbmeter.cmd` on Windows and `./dbmeter` elsewhere. Pass JVM options with `DBMETER_OPTS` (for example `-Xmx2g`), and add `-Ddbmeter.debug=true` to get stack traces.

Drivers live in `drivers/`. The examples are:

- **`examples/h2`**: runs out of the box against an in-memory H2 database.
- **`examples/postgres`**: a small PostgreSQL schema. The password comes from `PGPASSWORD`.
- **`examples/sales-agg`**: large aggregations over a deterministic 5M-row star schema on PostgreSQL. It uses joins, `ROLLUP`, window functions, `COUNT(DISTINCT)`, CTEs and a parameterized query. `gen_expected.py` recomputes every expected result in Python from the same formulas as the schema, so `check` validates the database against an independent implementation. Run `check` first, because it builds the data. After that, run `load`.
- **`examples/ramsql`**: [RAMSQL](https://github.com/SB0LTD/ramsql) through the stock PostgreSQL JDBC driver. Start it with `ramsql.exe --memory --insecure-local --port 5433`. RAMSQL 0.3.0-alpha.2 has no joins or aggregates yet, and its default profile allows 512 rows per table. So the workload uses PK lookups, filtered scans, single-row updates and transactions. `gen.py` produces the data and the expected results. To run the same workload on PostgreSQL for comparison, add `--set jdbc.url=jdbc:postgresql://localhost:5432/postgres --set "connection.init.sql=CREATE SCHEMA IF NOT EXISTS dbmeter_ramsql; SET search_path TO dbmeter_ramsql" --set check.z_gap_aggregates.enabled=false --set run.databaseLabel=`.

- **`examples/sales-agg/irdb.properties`**: the same 5M-row workload and the same expected results on InMemory.Net IRDB, through the adapter described below. The queries in `sql-irdb/` change only what IRDB's dialect requires. IRDB has no `ROLLUP` (q4 uses `UNION ALL` instead), no `to_char` (it uses `FORMAT`), no `DATE '…'` literals, no `NULLS LAST`, and no window functions over CTE columns. Two aliases don't work as written: `rows` is reserved, and an alias of `month` comes back as `m`. The data is copied from PostgreSQL with `CopyToIrdb`; the command is in the config header.

`tools/` (git-ignored) holds local database servers: the RAMSQL release binary and the InMemory.Net IRDB server. IRDB's subquery cache is on by default. It made q5 look about 10× faster when the same query was repeated, so `irdb.ini` sets `nocache=true` for honest load numbers.

### IRDB JDBC adapter (`irdb-jdbc/`)

IRDB ships only a Java client, `jirdb` (`irdb.IPClient`), not a JDBC driver. `irdb-jdbc/` is a small JDBC driver on top of that client. The build creates `build/irdb-jdbc.jar` whenever `drivers/jirdb-*.jar` is present. jirdb itself needs `fastutil` (`drivers/fastutil-8.5.15.jar`) at runtime.

- **URL:** `jdbc:irdb://host[:port]/database[?encrypt=true&timeout=SECONDS]`. The port defaults to 5060; the properties are `user` (default `default`) and `password` (default `!TopSecret`).
- **Statements:** queries, and prepared statements with `?` parameters, which run as server-side statements. IRDB has no transactions, so commit and rollback are accepted as no-ops. SQL comments are stripped before sending, because IRDB's parser rejects them.
- **Loading data:** `dbmeter.irdb.CopyToIrdb` copies tables or query results from any JDBC source into an IRDB database through jirdb's temp-DB API (`createTempDB`, `addTableInChunksToTempDB`, `saveTempDB`).
- **dbMeter config:** `driver.jar=../../build/irdb-jdbc.jar, ../../drivers/jirdb-2025-02-02.jar, ../../drivers/fastutil-8.5.15.jar`

## Configuration (`.properties`)

Relative paths resolve against the config file's directory. Use forward slashes in paths, because `\` is an escape character in `.properties` files. Values can reference `${env:NAME}`, `${sys:name}` or `${other.key}`, each with an optional default: `${env:PGHOST:-localhost}`. You can override any key from the command line with `--set key=value`.

### Connection

| key | meaning |
|---|---|
| `driver.jar` | driver jar(s) or a directory of jars, separated by `,` or `;` |
| `driver.class` | driver class; optional when the driver self-registers (JDBC 4) |
| `jdbc.url` | JDBC URL (required) |
| `jdbc.user`, `jdbc.password` | credentials |
| `jdbc.prop.<name>` | any other driver property, such as `jdbc.prop.ssl=true` |
| `connection.init.sql` / `.file` | SQL run on every new connection |
| `connection.isolation` | `READ_COMMITTED`, `REPEATABLE_READ`, `SERIALIZABLE`, ... |
| `connection.testQuery` | query that `info` runs |
| `sql.delimiter` | statement delimiter for scripts (default `;`, see below) |

### SQL files

Scripts are split on `;`. Semicolons inside strings, quoted identifiers, comments and PostgreSQL `$$` bodies are handled correctly. For Oracle PL/SQL, set `delimiter=/`; for SQL Server, `delimiter=GO`. With either of these, the delimiter has to be alone on its own line. `delimiter=none` sends the whole file as a single statement. You can set the delimiter per file with `<prefix>.delimiter`.

Parameters use `:{name[:type]}` and are bound as real JDBC parameters, so statements are prepared once per connection:

- `:{customer_id:int}`: a column from the scenario's CSV params file (`scenario.x.param.<name>` for checks)
- `:{$rand(1,1000)}`, `:{$uuid}`, `:{$thread}`, `:{$iter}`, `:{$now}`: generated values
- types: `string` (default), `int`, `long`, `double`, `decimal`, `bool`, `date`, `time`, `timestamp`, `object`. An unquoted `NULL` in the CSV binds SQL NULL.

### Load test

| key | default | meaning |
|---|---|---|
| `load.threads` | 1 | concurrent connections on this node |
| `load.duration` | 30s | measured time (`500ms`, `30s`, `5m`, `1h`) |
| `load.warmup` | 0 | unmeasured time after the ramp-up |
| `load.rampUp` | 0 | thread start-up spread over this time |
| `load.iterations` | – | run a fixed number of measured iterations per thread instead of a duration |
| `load.warmupIterations` | 0 | unmeasured iterations per thread (with `iterations`) |
| `load.fetchSize`, `load.queryTimeout` | – | JDBC fetch size and query timeout |
| `load.reportInterval` | 5s | how often progress is printed |
| `load.scenarios` | all | comma-separated subset (same as `--only`) |
| `load.setup.*` / `load.teardown.*` | – | `.file`/`.sql` scripts, `.continueOnError=true` |
| `load.startAt` | now | synchronized start time (same as `--start-at`) |
| `run.id`, `run.node`, `run.out` | timestamp, host name, `reports` | report identity and location |

Each scenario is defined as `scenario.<name>.*`:

| key | meaning |
|---|---|
| `.file` / `.sql` | the statements; all of them run in order and are timed together |
| `.weight` | relative frequency in the random mix (default 1; 0 disables) |
| `.transaction` | run the statements in one transaction (default: true when there is more than one) |
| `.rollback` | roll back instead of commit, so write tests leave no data behind |
| `.params.file`, `.params.mode` | CSV file with a header row; `sequential` (shared cursor) or `random` |
| `.thinkTime` | pause after each execution |

Every result set is read to the end, so each timing includes the fetch. Errors are counted and grouped by message, the transaction is rolled back, and a broken connection is reopened.

### Checks

Each check is defined as `check.<name>.*`. Checks run in name order on one connection, between `check.setup.*` and `check.teardown.*`. Expectations apply to the result of the last statement:

| key | meaning |
|---|---|
| `.file` / `.sql`, `.param.<name>` | statements and their parameter values |
| `.expect.value` | first column of the first row (`NULL` for null) |
| `.expect.rows` | number of rows |
| `.expect.csv` (+ `.expect.ordered=true`) | full result vs. a CSV file with header; unordered by default |
| `.expect.updateCount` | rows affected |
| `.expect.error` | `true` or a substring of the expected error message |
| `.expect.maxMs` | fails if the check takes longer than this |
| `.rollback` | run in a transaction and roll it back |

Numbers compare by value, so `63625.00` equals `63625`. The exit code is 1 if any check fails, which makes `check` suitable for CI together with `--junit`.

## Distributed load (several machines, one database)

1. Copy the jar, the driver and the config to each machine. Clocks must be NTP-synced, because the timelines align on wall-clock seconds.
2. Start every node with the **same** `--run-id` and `--start-at`, a unique `--node`, and a shared `--out` folder if you have one. Run setup on one node only:

   ```
   # node 1
   dbmeter load -c pg.properties --run-id r42 --node app1 --start-at 2026-10-23T14:00:00Z --out //fileserver/dbmeter/r42
   # nodes 2..n
   dbmeter load -c pg.properties --run-id r42 --node app2 --start-at 2026-10-23T14:00:00Z --out //fileserver/dbmeter/r42 --skip-setup
   ```

   Each node opens its connections, waits for the start time, ramps up, warms up and measures over the same window. It writes `<node>.json`, `<node>-summary.txt` and `<node>-timeline.csv`.
3. Merge the reports: `dbmeter merge //fileserver/dbmeter/r42`. This writes `merged.json`, `merged-summary.txt` and `merged-timeline.csv`. The summary lists each node's host, thread and CPU count and its window offset, so you can spot a node that started late.

Remember that the database sees `threads × nodes` connections.

## Comparing databases

`dbmeter compare <report.json|dir>... [--out dir]` shows runs of the same workload side by side, for example one run per database. For each run it shows tps, p50, p99 and errors for every scenario. It also lists scenarios that never succeeded, with their first error, so a database that can't run part of a workload shows up in the table instead of being left out. A directory argument stands for its `merged.json`, or its only node report. `--out` also writes `compare.txt` and `compare.csv`.

```
dbmeter compare reports/pg-agg reports/irdb-agg-nocache reports/ramsql-agg --out reports/compare-agg
```

Some servers report another product's identity; RAMSQL, for example, reports PostgreSQL 16.0. For those, set `run.databaseLabel` so that reports name the real database.

## Reports

- `*.json`: the full result. Per-scenario latency histograms (log-linear buckets with ~3% precision, in microseconds), error messages, and a per-second timeline of `[count, errors, sumMicros, maxMicros]`. Node and database metadata are included too.
- `*-summary.txt`: the console summary table.
- `*-timeline.csv`: per-second throughput, average latency and max latency, per scenario plus TOTAL. You can load it into a spreadsheet or Grafana to chart it.
