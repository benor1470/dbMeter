# Reviewer requirement coverage

The original reviewer sources remain at `630fbb13be21bd4bbdf7afe4b438ce771a8b3fc0`.
Every campaign verifies the frozen normalized Java, adapter, SQL, properties,
data and expected-CSV manifest. Changes in `ci/` do not redefine those inputs.

| Requirement | Mandatory evidence |
| --- | --- |
| Original 500-row JDBC example | Six named checks on RAMSQL and native PostgreSQL: full row count, parameterized PK, ordered filtered CSV, INSERT update count, rollback visibility and aggregate count |
| Original size-scaling data | All six tables: 100, 512, 1K, 10K, 100K and 1M rows; all 24 named checks on each engine |
| Capacity beyond alpha.2 | Original 200-byte TEXT, nine-column and twenty-additional-table checks; never omitted from the decision |
| Original subset sales | 1M facts and five dimensions; all seven original checks, including the independent totals and six query CSVs |
| Original full sales | Actual 5M facts and five dimensions; all seven original checks, including joins, CTEs, windows, DISTINCT aggregates and ROLLUP |
| Native references | Pinned PostgreSQL and IRDB with all six original primary-key columns clustered; vendor adapter/dialects retained; IRDB vendor query cache enabled and disclosed |
| All 16 timed cases | 100K/1M PK/filter/top-N at 1/8 clients, plus both original weighted six-query sales mixes at 1/8 clients |
| Repeated measurements | Three 5s ramp / 15s warmup / 60s measurement trials per engine/case; fresh JVMs, rotating engines, one database engine active during timing |
| Unchanged significant-advantage gate | At least 2x the faster reference's median throughput, minimum RAMSQL trial above both reference maxima, p99 no more than 10% worse than either reference, zero errors |
| Provenance and completeness | Actual image/source/toolchain/driver hashes, server arguments, original input manifest, complete JUnit and JSON reports, hardware/pressure snapshots and same-run/attempt identity |

The legacy `z_gap_aggregates` query still executes. Its alpha.2 expectation of
an error is explicitly overridden to `COUNT(*) = 500` on both engines. No legacy
check is disabled; original files and SQL remain unchanged. The override and
exact commands are recorded in `original-jdbc-correctness`.

The 28 November candidate pins RAMSQL main `17f3301e` and its exact ZPM archive
`61f24e5e`. It uses the hosted release capacity profile (32 tables, nine columns,
200-byte inline cells and integral version banks) and **no application read
cache**. It is a source qualification, not qualification of a later downloaded
release. Identical baseline/candidate pins avoid a redundant A/B build.
IRDB retains its enabled vendor subquery cache. The restored reference is saved,
reloaded and checked after index construction; all original JDBC correctness
checks then run again. Missing index/cache/vendor provenance blocks the gate.

Original fetch-all scenarios and the 512-row update mix are not part of the
established 16-case performance gate; this document does not claim their speed.
The legacy correctness job verifies updates and rollback. The separate 100M
capacity/recovery and SQL Server workflows are additional qualifications, not
substitutes for the reviewer suites or proof of general PostgreSQL parity.

Keep this PR unmerged until the complete fresh advantage decision is green.
Historical wins on selected queries, cached diagnostics and a passing release
matrix cannot establish this candidate's correctness or 2x performance.
