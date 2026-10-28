# Live RAMSQL comparison

This workflow runs the original dbMeter Java implementation, SQL, workload
weights and independent expected CSVs against real RAMSQL, native PostgreSQL
and IRDB servers. No Java engine, adapter or query implementation is replaced.
It adds orchestration and presentation under `ci/`; the original example files
remain unchanged. Generated files are checked against tracked inputs after LF
normalization. Raw JDBC reports, checks, logs, hashes, versions, server arguments
and failures are retained as Actions artifacts.

The Actions job summaries highlight measured RAMSQL wins and show every declared
case, including losses and unscored/unsupported cases. **A highlighted row is
not permission to merge this PR or make a general README claim.**

## Running before merge

- Every same-repository PR runs a short smoke comparison: one trial, two seconds
  warmup and three seconds measured. Smoke results can never pass the advantage
  gate. Fork PRs receive no private driver secrets.
- Add the PR label **`benchmark:qualify`** to run qualification without first
  merging a new workflow onto the default branch. Qualification uses three
  trials, the original five-second ramp, 15-second warmup and 60-second measured
  phases, with fresh JVMs and rotating engine order. Reapply the label or push a
  candidate update for a new run. After the workflow exists on the default branch,
  `workflow_dispatch` provides the same choice.
- RAMSQL's source/toolchain and vendor downloads are checksum pinned in
  `versions.json`. Update that reviewed file to qualify a different candidate;
  a floating `latest` build is never silently substituted.
- The supplied IRDB Java client is restored from five private repository secrets
  `IRDB_JDBC_PART_1` through `IRDB_JDBC_PART_5`, then SHA-256 checked. It is not
  committed, attached to a public release or included in result artifacts. The
  reviewer's private INI/password is never used: CI creates its own synthetic-data
  server configuration with `nocache=true` and the vendor's public lab defaults.

## Fixed comparison matrix

| Suite | Dataset | Timed cases |
| --- | --- | --- |
| Size scaling | All original six tables, 100 through 1M rows | 100k and 1M PK lookup, filter and top-N; 1/8 clients |
| Sales subset | Original 1M facts and five dimensions | Original weighted six-query mix; 1/8 clients |
| Full sales | Actual 5M facts and five dimensions | Original weighted six-query mix; 1/8 clients |

Each suite must pass all its original data checks on all three engines. The
size suite also retains the original RAMSQL 200-byte text, 9-column and 20-table
capability probes as explicit merge blockers, separate from the data checks.
Their failure does not prevent displaying valid small-read timings. No capacity
probe or full-query failure can disappear from the overall advantage decision.

RAMSQL's old full-suite sample schema is bypassed: PostgreSQL generates the real
data, which is copied through the original `CopyToIrdb` utility and exact CSV
export/50-row SQL batches into RAMSQL. The full data transfer checks table counts
and retains hashes, not raw data. Full-suite IRDB uses the original dialect
files. The subset's sole SQL adaptation removes `DATE` from two ISO literals for
IRDB, matching its existing full-suite dialect; expected CSVs stay unchanged.
The generated RAMSQL loaders begin with unconditional `DROP TABLE` statements
that the examples normally continue past on an empty server. CI omits only that
known missing-table prelude in an ephemeral transfer copy, records both hashes,
and runs every unchanged CREATE/INSERT with `ON_ERROR_STOP` enabled. Original
loaders are untouched; later/unrelated DROP statements are rejected.

Setup and correctness may have two services resident for data transfer. Every
server stops before timing. Timed cases run with **one database engine active**;
RAMSQL uses the runner's available CPU affinity, up to its eight configured
workers, and both references retain their normal engine settings. RAMSQL's
PostgreSQL uses Docker **host networking**, not a bridge/forwarded port: all
three JDBC connections traverse the same host-loopback boundary. Earlier setup
debug runs using a forwarded port are not qualifying performance evidence.
RAMSQL's
default text layout is used; no narrower binary is substituted to fit CI.
These are memory-resident read comparisons, not a durable-write benchmark.

Default private GitHub runners have 8 GB RAM. Full default-layout RAMSQL data
qualification currently needs a larger Linux machine: set repository variable
`RAMSQL_BENCHMARK_RUNNER` to an existing suitable runner label. The workflow uses
RAMSQL's actual native capacity admission, captures its machine budget and
reports rejected reservations as gaps; it imposes no fixed RAM-size threshold.
The dataset is never shrunk.
This prevents a small hosted-runner screen from being presented as 5M/100M
production qualification. See [GitHub runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

## Permission to merge and claim an advantage

The **Significant advantage required before merge** job fails unless all three
suite artifacts, all original checks/capability probes and every declared case
qualify. For each case RAMSQL must:

1. Complete three original-duration trials with zero SQL errors and consistent
   source/input/binary provenance.
2. Reach at least **2× the median throughput of the faster reference**.
3. Have its lowest throughput trial exceed both references' highest trial.
4. Have median p99 latency no more than 10% worse than either reference.

Rates use actual recorded measured windows, including drain time. Mixed queries
must all execute successfully. Missing engines, reports, trials, scenarios,
checks, memory capacity or unsupported SQL fail closed. Three trials establish
a bounded reproducible gate, not a statistical confidence interval or superiority
over every possible SQL workload.

**Keep the PR unmerged while this gate fails.** Do not add README advantage
claims or a success badge from smoke results. After a qualifying workflow run,
review its exact candidate hash, hardware, all tables and `advantage-decision`
artifact, then merge and cite that immutable run in any README claim. Scope the
claim to this measured matrix and disclose remaining production/durability/100M
limitations. The workflow has read-only repository permissions and cannot merge
itself or edit the README.
