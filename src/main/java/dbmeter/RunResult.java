package dbmeter;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/** Results of one node (or, after merging, of all nodes) of a load run. */
final class RunResult {
    static final int FORMAT = 1;

    String runId;
    final List<Map<String, Object>> nodes = new ArrayList<>();
    final Map<String, Object> database = new LinkedHashMap<>();
    long measuredStartMs;
    long measuredEndMs;
    final Map<String, ScenarioStats> scenarios = new LinkedHashMap<>();

    /** run.databaseLabel if set (for servers that report another product's identity), else product + version. */
    String databaseName() {
        Object label = database.get("label");
        if (label != null) {
            return label + " [reports as " + database.get("product") + " " + database.get("productVersion") + "]";
        }
        return database.get("product") + " " + database.get("productVersion");
    }

    double windowSeconds() {
        return Math.max(0.001, (measuredEndMs - measuredStartMs) / 1000.0);
    }

    ScenarioStats total() {
        ScenarioStats t = new ScenarioStats();
        scenarios.values().forEach(t::merge);
        return t;
    }

    void merge(RunResult o) {
        nodes.addAll(o.nodes);
        if (database.isEmpty()) {
            database.putAll(o.database);
        }
        measuredStartMs = Math.min(measuredStartMs, o.measuredStartMs);
        measuredEndMs = Math.max(measuredEndMs, o.measuredEndMs);
        for (Map.Entry<String, ScenarioStats> e : o.scenarios.entrySet()) {
            scenarios.computeIfAbsent(e.getKey(), k -> new ScenarioStats()).merge(e.getValue());
        }
    }

    /** Writes base.json, base-summary.txt and base-timeline.csv to dir and returns the JSON path. */
    Path writeFiles(Path dir, String base) throws IOException {
        Files.createDirectories(dir);
        Path json = dir.resolve(base + ".json");
        Util.writeFile(json, Json.write(toJson()));
        Util.writeFile(dir.resolve(base + "-summary.txt"), summary());
        Util.writeFile(dir.resolve(base + "-timeline.csv"), timelineCsv());
        return json;
    }

    Map<String, Object> toJson() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("dbmeterReport", FORMAT);
        m.put("runId", runId);
        m.put("measuredStart", measuredStartMs);
        m.put("measuredEnd", measuredEndMs);
        m.put("nodes", nodes);
        m.put("database", database);
        Map<String, Object> sc = new LinkedHashMap<>();
        scenarios.forEach((k, v) -> sc.put(k, v.toJson()));
        m.put("scenarios", sc);
        return m;
    }

    static RunResult fromJson(Object o) {
        Map<String, Object> m = Json.obj(o);
        if (Json.num(m.get("dbmeterReport")) != FORMAT) {
            throw new IllegalArgumentException("not a dbmeter report (or unsupported version)");
        }
        RunResult r = new RunResult();
        r.runId = (String) m.get("runId");
        r.measuredStartMs = Json.num(m.get("measuredStart"));
        r.measuredEndMs = Json.num(m.get("measuredEnd"));
        for (Object n : Json.list(m.get("nodes"))) {
            r.nodes.add(new LinkedHashMap<>(Json.obj(n)));
        }
        r.database.putAll(Json.obj(m.get("database")));
        Json.obj(m.get("scenarios")).forEach((k, v) -> r.scenarios.put(k, ScenarioStats.fromJson(Json.obj(v))));
        return r;
    }

    String summary() {
        StringBuilder sb = new StringBuilder();
        double window = windowSeconds();
        sb.append(String.format(Locale.ROOT, "Run:       %s%n", runId));
        sb.append(String.format(Locale.ROOT, "Database:  %s (driver %s %s)%n", databaseName(),
                database.get("driver"), database.get("driverVersion")));
        sb.append(String.format(Locale.ROOT, "Window:    %s .. %s (%.1f s)%n", Instant.ofEpochMilli(measuredStartMs),
                Instant.ofEpochMilli(measuredEndMs), window));
        long totalThreads = 0;
        for (Map<String, Object> n : nodes) {
            totalThreads += Json.num(n.get("threads"));
        }
        sb.append(String.format(Locale.ROOT, "Nodes:     %d node(s), %d thread(s) total%n", nodes.size(), totalThreads));
        for (Map<String, Object> n : nodes) {
            long start = Json.num(n.get("measuredStart"));
            long end = Json.num(n.get("measuredEnd"));
            sb.append(String.format(Locale.ROOT, "  %-20s host %-18s %3d threads %3d cpus  window %+.1fs .. %+.1fs%n",
                    n.get("node"), n.get("host"), Json.num(n.get("threads")), Json.num(n.get("cpus")),
                    (start - measuredStartMs) / 1000.0, (end - measuredStartMs) / 1000.0));
        }
        sb.append('\n');
        String fmt = "%-24s %10s %8s %11s %10s %9s %9s %9s %9s %9s %9s%n";
        sb.append(String.format(Locale.ROOT, fmt, "scenario", "count", "errors", "rows", "tps", "avg ms",
                "p50 ms", "p90 ms", "p95 ms", "p99 ms", "max ms"));
        for (Map.Entry<String, ScenarioStats> e : scenarios.entrySet()) {
            row(sb, fmt, e.getKey(), e.getValue(), window);
        }
        if (scenarios.size() > 1) {
            row(sb, fmt, "TOTAL", total(), window);
        }
        boolean header = false;
        for (Map.Entry<String, ScenarioStats> e : scenarios.entrySet()) {
            for (Map.Entry<String, Long> m : e.getValue().errorMessages.entrySet()) {
                if (!header) {
                    sb.append("\nErrors:\n");
                    header = true;
                }
                sb.append(String.format(Locale.ROOT, "  %-20s %8dx  %s%n", e.getKey(), m.getValue(), m.getKey()));
            }
        }
        return sb.toString();
    }

    private static void row(StringBuilder sb, String fmt, String name, ScenarioStats s, double window) {
        Histogram h = s.latency;
        sb.append(String.format(Locale.ROOT, fmt, Util.truncate(name, 24), h.count, s.errors, s.rows,
                String.format(Locale.ROOT, "%.1f", h.count / window), Util.ms(h.mean()), Util.ms(h.percentile(50)),
                Util.ms(h.percentile(90)), Util.ms(h.percentile(95)), Util.ms(h.percentile(99)), Util.ms(h.max)));
    }

    /** Per-second throughput and latency, per scenario and in total, keyed by wall-clock second. */
    String timelineCsv() {
        StringBuilder sb = new StringBuilder("epoch_sec,time_utc,scenario,count,errors,avg_ms,max_ms\n");
        Map<String, ScenarioStats> all = new LinkedHashMap<>(scenarios);
        if (scenarios.size() > 1) {
            all.put("TOTAL", total());
        }
        TreeMap<Long, List<String>> lines = new TreeMap<>();
        for (Map.Entry<String, ScenarioStats> e : all.entrySet()) {
            for (Map.Entry<Long, long[]> t : e.getValue().timeline.entrySet()) {
                long[] v = t.getValue();
                lines.computeIfAbsent(t.getKey(), k -> new ArrayList<>()).add(String.format(Locale.ROOT,
                        "%d,%s,%s,%d,%d,%s,%s", t.getKey(), Instant.ofEpochSecond(t.getKey()), csv(e.getKey()),
                        v[0], v[1], v[0] == 0 ? "" : Util.ms(v[2] / v[0]), Util.ms(v[3])));
            }
        }
        lines.values().forEach(l -> l.forEach(x -> sb.append(x).append('\n')));
        return sb.toString();
    }

    private static String csv(String s) {
        return s.contains(",") || s.contains("\"") ? "\"" + s.replace("\"", "\"\"") + "\"" : s;
    }
}
