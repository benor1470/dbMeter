package dbmeter;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Log-linear latency histogram (microseconds) with ~3% relative precision. Bucket counts are kept, not samples,
 * so histograms from many threads and many machines merge exactly and percentiles stay correct after merging.
 */
final class Histogram {
    static final int SUB_BITS = 5;
    private static final int SUB = 1 << SUB_BITS;

    private final long[] counts = new long[(64 - SUB_BITS + 1) * SUB];
    long count;
    long sum;
    long min = Long.MAX_VALUE;
    long max;

    void record(long v) {
        if (v < 0) {
            v = 0;
        }
        counts[index(v)]++;
        count++;
        sum += v;
        if (v < min) {
            min = v;
        }
        if (v > max) {
            max = v;
        }
    }

    static int index(long v) {
        if (v < SUB) {
            return (int) v;
        }
        int shift = 63 - Long.numberOfLeadingZeros(v) - SUB_BITS;
        return (shift + 1) * SUB + (int) ((v >>> shift) - SUB);
    }

    private static long lowerBound(int idx) {
        if (idx < SUB) {
            return idx;
        }
        int shift = idx / SUB - 1;
        return ((long) (idx % SUB + SUB)) << shift;
    }

    private static long upperBound(int idx) {
        if (idx < SUB) {
            return idx;
        }
        int shift = idx / SUB - 1;
        return (((long) (idx % SUB + SUB + 1)) << shift) - 1;
    }

    long percentile(double p) {
        if (count == 0) {
            return 0;
        }
        long rank = Math.max(1, (long) Math.ceil(p / 100.0 * count));
        long seen = 0;
        for (int i = 0; i < counts.length; i++) {
            seen += counts[i];
            if (seen >= rank) {
                long mid = (lowerBound(i) + upperBound(i)) / 2;
                return Math.max(min, Math.min(max, mid));
            }
        }
        return max;
    }

    long mean() {
        return count == 0 ? 0 : sum / count;
    }

    long minOrZero() {
        return count == 0 ? 0 : min;
    }

    void merge(Histogram o) {
        for (int i = 0; i < counts.length; i++) {
            counts[i] += o.counts[i];
        }
        count += o.count;
        sum += o.sum;
        min = Math.min(min, o.min);
        max = Math.max(max, o.max);
    }

    Map<String, Object> toJson() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("subBits", SUB_BITS);
        m.put("count", count);
        m.put("sum", sum);
        m.put("min", minOrZero());
        m.put("max", max);
        Map<String, Object> buckets = new LinkedHashMap<>();
        for (int i = 0; i < counts.length; i++) {
            if (counts[i] != 0) {
                buckets.put(Integer.toString(i), counts[i]);
            }
        }
        m.put("buckets", buckets);
        return m;
    }

    static Histogram fromJson(Map<String, Object> m) {
        if (Json.num(m.get("subBits")) != SUB_BITS) {
            throw new IllegalArgumentException("unsupported histogram resolution " + m.get("subBits"));
        }
        Histogram h = new Histogram();
        h.count = Json.num(m.get("count"));
        h.sum = Json.num(m.get("sum"));
        h.max = Json.num(m.get("max"));
        h.min = h.count == 0 ? Long.MAX_VALUE : Json.num(m.get("min"));
        for (Map.Entry<String, Object> e : Json.obj(m.get("buckets")).entrySet()) {
            h.counts[Integer.parseInt(e.getKey())] = Json.num(e.getValue());
        }
        return h;
    }
}
