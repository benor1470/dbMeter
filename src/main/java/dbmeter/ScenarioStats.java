package dbmeter;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/** Latency histogram, error tally and per-second timeline for one scenario. Not thread safe: one per worker. */
final class ScenarioStats {
    private static final int MAX_MESSAGES = 50;

    final Histogram latency = new Histogram();
    long errors;
    long rows;
    final Map<String, Long> errorMessages = new LinkedHashMap<>();
    /** epoch second -> {count, errors, sumMicros, maxMicros}; wall-clock keys line up across machines. */
    final TreeMap<Long, long[]> timeline = new TreeMap<>();

    void success(long micros, long rowCount, long endMs) {
        latency.record(micros);
        rows += rowCount;
        long[] t = slot(endMs);
        t[0]++;
        t[2] += micros;
        t[3] = Math.max(t[3], micros);
    }

    void error(String message, long endMs) {
        errors++;
        slot(endMs)[1]++;
        addMessage(message, 1);
    }

    private long[] slot(long ms) {
        return timeline.computeIfAbsent(ms / 1000, k -> new long[4]);
    }

    private void addMessage(String m, long n) {
        if (!errorMessages.containsKey(m) && errorMessages.size() >= MAX_MESSAGES) {
            m = "(other errors)";
        }
        errorMessages.merge(m, n, Long::sum);
    }

    void merge(ScenarioStats o) {
        latency.merge(o.latency);
        errors += o.errors;
        rows += o.rows;
        o.errorMessages.forEach(this::addMessage);
        for (Map.Entry<Long, long[]> e : o.timeline.entrySet()) {
            long[] t = timeline.computeIfAbsent(e.getKey(), k -> new long[4]);
            long[] s = e.getValue();
            t[0] += s[0];
            t[1] += s[1];
            t[2] += s[2];
            t[3] = Math.max(t[3], s[3]);
        }
    }

    Map<String, Object> toJson() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("count", latency.count);
        m.put("errors", errors);
        m.put("rows", rows);
        m.put("latencyMicros", latency.toJson());
        m.put("errorMessages", new LinkedHashMap<>(errorMessages));
        Map<String, Object> tl = new LinkedHashMap<>();
        for (Map.Entry<Long, long[]> e : timeline.entrySet()) {
            List<Object> v = new ArrayList<>();
            for (long x : e.getValue()) {
                v.add(x);
            }
            tl.put(Long.toString(e.getKey()), v);
        }
        m.put("timeline", tl);
        return m;
    }

    static ScenarioStats fromJson(Map<String, Object> m) {
        ScenarioStats s = new ScenarioStats();
        s.latency.merge(Histogram.fromJson(Json.obj(m.get("latencyMicros"))));
        s.errors = Json.num(m.get("errors"));
        s.rows = Json.num(m.get("rows"));
        Json.obj(m.get("errorMessages")).forEach((k, v) -> s.errorMessages.put(k, Json.num(v)));
        for (Map.Entry<String, Object> e : Json.obj(m.get("timeline")).entrySet()) {
            List<Object> v = Json.list(e.getValue());
            long[] t = new long[4];
            for (int i = 0; i < 4 && i < v.size(); i++) {
                t[i] = Json.num(v.get(i));
            }
            s.timeline.put(Long.parseLong(e.getKey()), t);
        }
        return s;
    }
}
