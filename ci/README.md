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

- Performance decisions use **CI measurements only**. Local execution remains
  useful for correctness and profiling; local throughput cannot qualify a
  candidate or a README claim. Campaign execution requires GitHub Actions.
  The gate also requires every suite to come from the same run, attempt and
  exact checkout; results from different machines/runs cannot be pooled.
- Every same-repository PR update runs qualification without first merging the
  workflow onto the default branch. Qualification uses three
  trials, the original five-second ramp, 15-second warmup and 60-second measured
  phases, with fresh JVMs and rotating engine order on the same VM for each
  suite. Fork PRs receive no private driver secrets. After the workflow exists
  on the default branch, `workflow_dispatch` can request a smoke run explicitly;
  smoke results can never pass the advantage gate.
- Each campaign records the Actions run URL/attempt, runner identity/image,
  CPU model/affinity, cgroup limits, RAM/swap and load/pressure snapshots before
  and after timed cases. CI reduces local interference but does not guarantee
  identical physical hardware; compare engines within each campaign, rather
  than attributing cross-run raw-QPS differences to a code change.
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
workers, and both references retain their normal engine settings.
PostgreSQL uses Docker **host networking**, not a bridge/forwarded port: all
three JDBC connections traverse the same host-loopback boundary. Earlier setup
debug runs using a forwarded port are not qualifying performance evidence.
RAMSQL's default text layout is used; no narrower binary is substituted to fit CI.
These are memory-resident read comparisons, not a durable-write benchmark.

Standard public Linux runners provide 16 GB RAM and four vCPUs; private runners
provide 8 GB and two vCPUs. The repository is now public. Attempt 2 of run
40859947997 reports 16,760,344,576 bytes and four CPUs, loads the original full
5M dataset, and retains its four RAMSQL SQL-feature failures. Full-data admission
does not establish full SQL qualification. An existing larger Linux runner can
be selected through repository variable `RAMSQL_BENCHMARK_RUNNER`. The workflow uses
RAMSQL's actual native capacity admission, captures its machine budget and
reports rejected reservations as gaps; it imposes no fixed RAM-size threshold.
The dataset is never shrunk.
This prevents a small hosted-runner screen from being presented as 5M/100M
production qualification. See [GitHub runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

## Optimization diagnostics while SQL gaps remain

Separate `diagnostic-sales-subset` and `diagnostic-sales-full` jobs retain all
original full-suite checks, then independently check the existing customer-total
and store/category cases plus their original totals check on every engine.
Only when those checks pass do they time those two unchanged query files with
1/8 clients, three original-duration trials and rotating engine order. They use
the original 1M/5M datasets respectively; a full-data capacity rejection remains
visible and never becomes a smaller-dataset result.

Diagnostic artifacts are named `diagnostic-*`. The all-case gate downloads only
`live-*`, and explicitly rejects diagnostic campaigns. Selected-read tables
therefore support optimization decisions without hiding the remaining SQL gaps
or granting permission to merge. Neither Java/adapters nor original example
SQL/data/expected CSV files are changed.

## Same-runner candidate/control experiments

The `paired-sales-subset` job runs a separately pinned RAMSQL baseline and
candidate on one VM in AB / BA / AB order. Both artifacts must have identical
dependency-manifest hashes, toolchain pins and build arguments. For the typed
group experiment the two server commits differ only by the opt-in and changelog;
the dependency includes the same sparse-predicate path in both variants.

Each variant/pair loads fresh original 1M data and passes all seven original
checks before timing the original weighted mix and an explicitly pinned
original focused case at 1/8
clients. Every timing uses the original 5s ramp / 15s warmup / 60s measure and a
fresh JVM. Paired ratios, all three trial ranges, p99, raw histograms and runner
pressure snapshots are retained. Partial, reordered, erroring or mismatched
binary/source trials are not scored. No local throughput run supplies evidence.

Paired artifacts are named `paired-*`, declare `diagnostic_only=true` and
`merge_allowed=false`, and never enter the unchanged all-engine advantage gate.
They isolate an execution change without turning a selected-case improvement
into permission to merge or claim universal superiority.

`versions.json` pins `paired_focus` to an existing original subset scenario.
The first experiment used top-products for typed multi-aggregation; the next
uses segment/discount for dimension group IDs. Changing the diagnostic focus
does not change any original SQL, expected CSV, weighted mix or advantage-gate
case. The focused scenario and all trial settings remain in every report.

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
