package dbmeter;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.LongAdder;

/**
 * Runs the weighted scenario mix on N threads, each with its own connection. Timeline: threads start spread over
 * the ramp-up, then warm up (not measured), then measure for the duration - or, with load.iterations, each thread
 * runs a fixed number of measured iterations after load.warmupIterations. All nodes of a distributed run use the
 * same start time, so their measurement windows line up.
 */
final class LoadRunner {
    private final Db db;
    private final List<Scenario> scenarios;
    private final String runId;
    private final String node;
    private final long startAtMs;

    private final int threads;
    private final long durationNanos;
    private final long warmupNanos;
    private final long rampUpNanos;
    private final long iterations;
    private final long warmupIterations;
    private final long reportIntervalMs;
    private final int fetchSize;
    private final int queryTimeoutSec;
    private final int totalWeight;
    private final Map<String, Object> settings = new LinkedHashMap<>();

    private final LongAdder ops = new LongAdder();
    private final LongAdder errs = new LongAdder();
    private final AtomicInteger active = new AtomicInteger();
    private final CountDownLatch completed = new CountDownLatch(1);
    private volatile boolean stop;
    private Thread shutdownHook;
    private long baseNanos;
    private long measureStartNanos;
    private long endNanos;

    LoadRunner(Config c, Db db, List<Scenario> scenarios, String runId, String node, long startAtMs) {
        this.db = db;
        this.scenarios = scenarios;
        this.runId = runId;
        this.node = node;
        this.startAtMs = startAtMs;
        threads = c.getInt("load.threads", 1);
        if (threads < 1) {
            throw new ConfigException("load.threads must be >= 1");
        }
        iterations = c.getLong("load.iterations", 0);
        warmupIterations = c.getLong("load.warmupIterations", 0);
        Duration duration = c.getDuration("load.duration", Duration.ofSeconds(30));
        Duration warmup = c.getDuration("load.warmup", Duration.ZERO);
        Duration rampUp = c.getDuration("load.rampUp", Duration.ZERO);
        durationNanos = duration.toNanos();
        warmupNanos = warmup.toNanos();
        rampUpNanos = rampUp.toNanos();
        reportIntervalMs = c.getDuration("load.reportInterval", Duration.ofSeconds(5)).toMillis();
        fetchSize = c.getInt("load.fetchSize", 0);
        queryTimeoutSec = (int) c.getDuration("load.queryTimeout", Duration.ZERO).getSeconds();
        totalWeight = scenarios.stream().mapToInt(s -> s.weight).sum();

        settings.put("threads", threads);
        if (iterations > 0) {
            settings.put("iterations", iterations);
            settings.put("warmupIterations", warmupIterations);
        } else {
            settings.put("duration", duration.toString());
            settings.put("warmup", warmup.toString());
        }
        settings.put("rampUp", rampUp.toString());
        databaseLabel = c.get("run.databaseLabel");
    }

    private final String databaseLabel;

    RunResult run() throws Exception {
        System.out.printf("opening %d connection(s)...%n", threads);
        List<Worker> workers = new ArrayList<>();
        Map<String, Object> dbInfo;
        try {
            for (int i = 0; i < threads; i++) {
                workers.add(new Worker(i, db.connect()));
            }
            dbInfo = Db.describe(workers.get(0).con);
        } catch (SQLException | RuntimeException e) {
            workers.forEach(w -> Util.closeQuietly(w.con));
            throw e;
        }
        System.out.printf("connected to %s %s%n", dbInfo.get("product"), dbInfo.get("productVersion"));

        long delayMs = startAtMs - System.currentTimeMillis();
        if (delayMs > 0) {
            System.out.printf("waiting for start at %s (in %.1f s)%n", Instant.ofEpochMilli(startAtMs), delayMs / 1000.0);
        } else if (delayMs < -2000) {
            System.out.printf("warning: started %.1f s after the scheduled start time%n", -delayMs / 1000.0);
        }
        baseNanos = System.nanoTime() + delayMs * 1_000_000L;
        measureStartNanos = baseNanos + rampUpNanos + warmupNanos;
        endNanos = measureStartNanos + durationNanos;

        shutdownHook = new Thread(() -> {
            stop = true;
            try {
                completed.await(30, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
                // exiting anyway
            }
        });
        Runtime.getRuntime().addShutdownHook(shutdownHook);

        List<Thread> threadList = new ArrayList<>();
        for (Worker w : workers) {
            Thread t = new Thread(w, "dbmeter-worker-" + w.index);
            t.start();
            threadList.add(t);
        }
        progressLoop(threadList);

        RunResult result = new RunResult();
        result.runId = runId;
        result.database.putAll(dbInfo);
        if (databaseLabel != null && !databaseLabel.isEmpty()) {
            result.database.put("label", databaseLabel);
        }
        long measuredStart = Long.MAX_VALUE;
        long measuredEnd = 0;
        for (Worker w : workers) {
            Util.closeQuietly(w.con);
            if (w.fatal != null) {
                System.out.printf("worker %d failed: %s%n", w.index, Util.describe(w.fatal));
            }
            if (w.measureBeganMs > 0) {
                measuredStart = Math.min(measuredStart, w.measureBeganMs);
                measuredEnd = Math.max(measuredEnd, w.finishedMs);
            }
        }
        if (iterations <= 0) {
            measuredStart = startAtMs + (rampUpNanos + warmupNanos) / 1_000_000L;
        }
        if (measuredEnd == 0) {
            measuredStart = measuredEnd = System.currentTimeMillis();
        }
        result.measuredStartMs = measuredStart;
        result.measuredEndMs = measuredEnd;
        for (int i = 0; i < scenarios.size(); i++) {
            ScenarioStats merged = new ScenarioStats();
            for (Worker w : workers) {
                merged.merge(w.stats[i]);
            }
            result.scenarios.put(scenarios.get(i).name, merged);
        }

        Map<String, Object> n = new LinkedHashMap<>();
        n.put("node", node);
        n.put("host", Util.hostName());
        n.put("threads", threads);
        n.put("cpus", Runtime.getRuntime().availableProcessors());
        n.put("os", System.getProperty("os.name") + " " + System.getProperty("os.version"));
        n.put("java", System.getProperty("java.version"));
        n.put("startAt", Instant.ofEpochMilli(startAtMs).toString());
        n.put("measuredStart", measuredStart);
        n.put("measuredEnd", measuredEnd);
        n.put("settings", settings);
        result.nodes.add(n);
        return result;
    }

    /** Lets a Ctrl+C shutdown proceed once the report has been written. */
    void complete() {
        completed.countDown();
        if (shutdownHook != null) {
            try {
                Runtime.getRuntime().removeShutdownHook(shutdownHook);
            } catch (IllegalStateException ignored) {
                // already shutting down
            }
        }
    }

    private void progressLoop(List<Thread> threadList) throws InterruptedException {
        long lastOps = 0;
        long lastPrint = System.nanoTime();
        while (true) {
            boolean alive = false;
            for (Thread t : threadList) {
                t.join(200);
                if (t.isAlive()) {
                    alive = true;
                    break;
                }
            }
            long now = System.nanoTime();
            if (!alive || now - lastPrint >= reportIntervalMs * 1_000_000L) {
                long total = ops.sum();
                double secs = (now - lastPrint) / 1e9;
                System.out.printf(Locale.ROOT, "[%7.1fs] %-8s ops %10d  %9.1f/s  errors %6d  active %d/%d%n",
                        (now - baseNanos) / 1e9, phase(now), total, (total - lastOps) / Math.max(secs, 0.001),
                        errs.sum(), active.get(), threads);
                lastOps = total;
                lastPrint = now;
            }
            if (!alive) {
                return;
            }
        }
    }

    private String phase(long now) {
        if (stop) {
            return "stopping";
        }
        if (now < baseNanos + rampUpNanos) {
            return "ramp-up";
        }
        if (iterations > 0) {
            return active.get() == 0 ? "done" : "running";
        }
        if (now < measureStartNanos) {
            return "warmup";
        }
        return now < endNanos ? "measure" : "done";
    }

    private final class Worker implements Runnable, BoundSql.ParamContext {
        final int index;
        Connection con;
        final ScenarioStats[] stats = new ScenarioStats[scenarios.size()];
        private final Map<BoundSql, PreparedStatement> cache = new IdentityHashMap<>();
        private Boolean autoCommit;
        private long iteration;
        volatile long measureBeganMs;
        volatile long finishedMs;
        volatile Throwable fatal;

        Worker(int index, Connection con) {
            this.index = index;
            this.con = con;
            for (int i = 0; i < stats.length; i++) {
                stats[i] = new ScenarioStats();
            }
        }

        @Override
        public int thread() {
            return index;
        }

        @Override
        public long iteration() {
            return iteration;
        }

        @Override
        public void run() {
            Thread.currentThread().setContextClassLoader(db.loader);
            sleepUntil(baseNanos + rampUpNanos * index / threads);
            active.incrementAndGet();
            try {
                if (iterations > 0) {
                    for (long i = 0; i < warmupIterations && !stop; i++) {
                        execute(false);
                    }
                    measureBeganMs = System.currentTimeMillis();
                    for (long i = 0; i < iterations && !stop; i++) {
                        execute(true);
                    }
                } else {
                    while (!stop) {
                        long now = System.nanoTime();
                        if (now >= endNanos) {
                            break;
                        }
                        boolean measured = now >= measureStartNanos;
                        if (measured && measureBeganMs == 0) {
                            measureBeganMs = System.currentTimeMillis();
                        }
                        execute(measured);
                    }
                }
            } catch (Throwable t) {
                fatal = t;
            } finally {
                finishedMs = System.currentTimeMillis();
                active.decrementAndGet();
            }
        }

        private Scenario pick() {
            if (scenarios.size() == 1) {
                return scenarios.get(0);
            }
            int r = ThreadLocalRandom.current().nextInt(totalWeight);
            for (Scenario s : scenarios) {
                r -= s.weight;
                if (r < 0) {
                    return s;
                }
            }
            return scenarios.get(scenarios.size() - 1);
        }

        private void execute(boolean measured) {
            Scenario s = pick();
            ScenarioStats st = stats[scenarios.indexOf(s)];
            Row row = s.params == null ? null : s.params.next();
            long t0 = System.nanoTime();
            try {
                if (con == null) {
                    con = db.connect();
                    autoCommit = null;
                }
                if (autoCommit == null || autoCommit == s.transaction) {
                    con.setAutoCommit(!s.transaction);
                    autoCommit = !s.transaction;
                }
                long rows = 0;
                for (BoundSql b : s.statements) {
                    PreparedStatement ps = prepare(b);
                    b.bind(ps, row, this);
                    rows += consume(ps, ps.execute());
                }
                if (s.transaction) {
                    if (s.rollback) {
                        con.rollback();
                    } else {
                        con.commit();
                    }
                }
                long micros = (System.nanoTime() - t0) / 1000;
                if (measured) {
                    st.success(micros, rows, System.currentTimeMillis());
                }
                ops.increment();
            } catch (Exception e) {
                if (measured) {
                    st.error(Util.truncate(Util.describe(e), 300), System.currentTimeMillis());
                }
                errs.increment();
                recover(s.transaction, e);
            }
            iteration++;
            if (s.thinkTimeMs > 0) {
                sleepUntil(System.nanoTime() + s.thinkTimeMs * 1_000_000L);
            }
        }

        private PreparedStatement prepare(BoundSql b) throws SQLException {
            PreparedStatement ps = cache.get(b);
            if (ps == null) {
                ps = con.prepareStatement(b.sql);
                if (fetchSize > 0) {
                    ps.setFetchSize(fetchSize);
                }
                if (queryTimeoutSec > 0) {
                    ps.setQueryTimeout(queryTimeoutSec);
                }
                cache.put(b, ps);
            }
            return ps;
        }

        /** Reads every result set to the end and returns rows fetched plus rows updated. */
        private long consume(Statement st, boolean isResultSet) throws SQLException {
            long rows = 0;
            while (true) {
                if (isResultSet) {
                    try (ResultSet rs = st.getResultSet()) {
                        while (rs.next()) {
                            rows++;
                        }
                    }
                } else {
                    int updated = st.getUpdateCount();
                    if (updated == -1) {
                        return rows;
                    }
                    rows += updated;
                }
                isResultSet = st.getMoreResults();
            }
        }

        /**
         * Rolls back and reconnects only when the connection itself failed (SQLState class 08 or closed).
         * isValid() is not used: some servers reject the driver's validation query, which would turn every
         * statement error into a reconnect and exhaust local ports.
         */
        private void recover(boolean transaction, Exception error) {
            if (con == null) {
                sleepUntil(System.nanoTime() + 500_000_000L);
                return;
            }
            try {
                if (transaction) {
                    con.rollback();
                }
                if (!isConnectionError(error) && !con.isClosed()) {
                    return;
                }
            } catch (SQLException ignored) {
                // reconnect below
            }
            cache.values().forEach(Util::closeQuietly);
            cache.clear();
            Util.closeQuietly(con);
            con = null;
        }

        private boolean isConnectionError(Exception error) {
            for (Throwable t = error; t != null; t = t.getCause()) {
                if (t instanceof SQLException) {
                    String state = ((SQLException) t).getSQLState();
                    if (state != null && state.startsWith("08")) {
                        return true;
                    }
                }
                if (t instanceof java.io.IOException) {
                    return true;
                }
            }
            return false;
        }

        private void sleepUntil(long nanos) {
            while (!stop) {
                long remaining = nanos - System.nanoTime();
                if (remaining <= 0) {
                    return;
                }
                try {
                    Thread.sleep(Math.min(200, remaining / 1_000_000L + 1));
                } catch (InterruptedException e) {
                    return;
                }
            }
        }
    }
}
