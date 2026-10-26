package dbmeter;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Side-by-side comparison of runs of the same workload, typically against different databases. */
final class Compare {
    private Compare() {
    }

    static String table(List<RunResult> runs) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < runs.size(); i++) {
            RunResult r = runs.get(i);
            sb.append(String.format(Locale.ROOT, "%s = %-22s %s, %d thread(s), %.1f s%n", letter(i), r.runId,
                    r.databaseName(), threads(r), r.windowSeconds()));
        }
        sb.append('\n');
        StringBuilder head = new StringBuilder(String.format(Locale.ROOT, "%-24s", "scenario"));
        for (int i = 0; i < runs.size(); i++) {
            head.append(String.format(Locale.ROOT, " | %-3s %9s %8s %8s %7s", letter(i), "tps", "p50 ms", "p99 ms", "errors"));
        }
        sb.append(head).append('\n');
        for (String scenario : scenarios(runs)) {
            sb.append(String.format(Locale.ROOT, "%-24s", Util.truncate(scenario, 24)));
            for (RunResult r : runs) {
                ScenarioStats s = stats(r, scenario);
                if (s == null) {
                    sb.append(String.format(Locale.ROOT, " | %-3s %9s %8s %8s %7s", "", "-", "-", "-", "-"));
                    continue;
                }
                Histogram h = s.latency;
                boolean ok = h.count > 0;
                sb.append(String.format(Locale.ROOT, " | %-3s %9s %8s %8s %7d", "",
                        String.format(Locale.ROOT, "%.1f", h.count / r.windowSeconds()),
                        ok ? Util.ms(h.percentile(50)) : "-", ok ? Util.ms(h.percentile(99)) : "-", s.errors));
            }
            sb.append('\n');
        }
        boolean header = false;
        for (int i = 0; i < runs.size(); i++) {
            for (Map.Entry<String, ScenarioStats> e : runs.get(i).scenarios.entrySet()) {
                if (e.getValue().latency.count > 0 || e.getValue().errorMessages.isEmpty()) {
                    continue;
                }
                if (!header) {
                    sb.append("\nScenarios that never succeeded (first error):\n");
                    header = true;
                }
                sb.append(String.format(Locale.ROOT, "  %s %-24s %s%n", letter(i), Util.truncate(e.getKey(), 24),
                        Util.truncate(e.getValue().errorMessages.keySet().iterator().next(), 150)));
            }
        }
        return sb.toString();
    }

    static String csv(List<RunResult> runs) {
        StringBuilder sb = new StringBuilder("run,database,scenario,count,errors,tps,avg_ms,p50_ms,p90_ms,p99_ms,max_ms\n");
        for (RunResult r : runs) {
            for (String scenario : scenarios(runs)) {
                ScenarioStats s = stats(r, scenario);
                if (s == null) {
                    continue;
                }
                Histogram h = s.latency;
                sb.append(String.format(Locale.ROOT, "%s,\"%s\",%s,%d,%d,%.2f,%s,%s,%s,%s,%s%n", r.runId,
                        r.databaseName().replace("\"", "\"\""), scenario, h.count, s.errors, h.count / r.windowSeconds(),
                        Util.ms(h.mean()), Util.ms(h.percentile(50)), Util.ms(h.percentile(90)),
                        Util.ms(h.percentile(99)), Util.ms(h.max)));
            }
        }
        return sb.toString();
    }

    private static Set<String> scenarios(List<RunResult> runs) {
        Set<String> names = new LinkedHashSet<>();
        for (RunResult r : runs) {
            names.addAll(r.scenarios.keySet());
        }
        if (runs.stream().anyMatch(r -> r.scenarios.size() > 1)) {
            names.add("TOTAL");
        }
        return names;
    }

    private static ScenarioStats stats(RunResult r, String scenario) {
        return scenario.equals("TOTAL") ? r.total() : r.scenarios.get(scenario);
    }

    private static long threads(RunResult r) {
        long t = 0;
        for (Map<String, Object> n : r.nodes) {
            t += Json.num(n.get("threads"));
        }
        return t;
    }

    private static String letter(int i) {
        return String.valueOf((char) ('A' + i));
    }
}
