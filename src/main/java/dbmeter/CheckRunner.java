package dbmeter;

import java.io.IOException;
import java.io.Reader;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Clob;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Correctness checks: each check.<name> runs its statements and compares the last result against the
 * expectations (row count, single value, full CSV result, update count, error, time limit).
 */
final class CheckRunner {
    private static final Set<String> RESERVED = Set.of("setup", "teardown");
    private static final Pattern NUMBER = Pattern.compile("[-+]?(\\d+\\.?\\d*|\\.\\d+)([eE][-+]?\\d+)?");
    private static final int MAX_ROWS = 100_000;
    private static final int MAX_DIFFS = 5;
    private static final BoundSql.ParamContext CONTEXT = new BoundSql.ParamContext() {
        @Override
        public int thread() {
            return 0;
        }

        @Override
        public long iteration() {
            return 0;
        }
    };

    private final Config c;
    private final Db db;

    CheckRunner(Config c, Db db) {
        this.c = c;
        this.db = db;
    }

    static final class Result {
        final String name;
        final List<String> failures = new ArrayList<>();
        long elapsedMicros;

        Result(String name) {
            this.name = name;
        }

        boolean passed() {
            return failures.isEmpty();
        }
    }

    /** The last result of a statement list: a result set or an update count. */
    private static final class Captured {
        List<String> columns;
        final List<List<String>> rows = new ArrayList<>();
        long updateCount = -1;
        boolean truncated;

        boolean isResultSet() {
            return columns != null;
        }
    }

    List<Result> run(Set<String> only, boolean skipSetup) throws Exception {
        List<String> names = new ArrayList<>();
        Set<String> defined = c.groupNames("check.");
        defined.removeAll(RESERVED);
        for (String n : only) {
            if (!defined.contains(n)) {
                throw new ConfigException("unknown check '" + n + "' (defined: " + defined + ")");
            }
        }
        for (String n : defined) {
            if ((only.isEmpty() || only.contains(n)) && c.getBool("check." + n + ".enabled", true)) {
                names.add(n);
            }
        }
        if (names.isEmpty()) {
            throw new ConfigException("no checks to run - define check.<name>.file or check.<name>.sql");
        }
        List<Result> results = new ArrayList<>();
        try (Connection con = db.connect()) {
            if (!skipSetup) {
                Scripts.run(con, c, "check.setup");
            }
            try {
                for (String name : names) {
                    Result r = runOne(con, name);
                    System.out.printf("%s  %-40s %10s ms%n", r.passed() ? "PASS" : "FAIL", name, Util.ms(r.elapsedMicros));
                    for (String f : r.failures) {
                        System.out.println("        " + f.replace("\n", "\n        "));
                    }
                    results.add(r);
                }
            } finally {
                if (!skipSetup) {
                    Scripts.run(con, c, "check.teardown");
                }
            }
        }
        return results;
    }

    private Result runOne(Connection con, String name) throws IOException {
        String p = "check." + name;
        Result r = new Result(name);
        List<BoundSql> statements = new ArrayList<>();
        try {
            for (String s : SqlScript.load(c, p)) {
                statements.add(BoundSql.parse(s));
            }
        } catch (ConfigException e) {
            r.failures.add(e.getMessage());
            return r;
        }
        Row params = Row.fromProperties(c.withPrefix(p + ".param."));
        boolean rollback = c.getBool(p + ".rollback", false);
        Captured last = null;
        Exception error = null;
        long t0 = System.nanoTime();
        try {
            if (rollback) {
                con.setAutoCommit(false);
            }
            for (BoundSql b : statements) {
                try (PreparedStatement ps = con.prepareStatement(b.sql)) {
                    b.bind(ps, params, CONTEXT);
                    Captured cap = capture(ps, ps.execute());
                    if (cap != null) {
                        last = cap;
                    }
                }
            }
        } catch (SQLException | RuntimeException e) {
            error = e;
        }
        r.elapsedMicros = (System.nanoTime() - t0) / 1000;
        try {
            if (rollback) {
                con.rollback();
                con.setAutoCommit(true);
            } else if (!con.getAutoCommit()) {
                con.commit();
            }
        } catch (SQLException e) {
            r.failures.add("rollback/commit failed: " + Util.describe(e));
        }
        evaluate(p, r, last, error);
        return r;
    }

    private static Captured capture(Statement st, boolean isResultSet) throws SQLException {
        Captured last = null;
        while (true) {
            if (isResultSet) {
                Captured cap = new Captured();
                try (ResultSet rs = st.getResultSet()) {
                    ResultSetMetaData md = rs.getMetaData();
                    int n = md.getColumnCount();
                    cap.columns = new ArrayList<>();
                    for (int i = 1; i <= n; i++) {
                        cap.columns.add(md.getColumnLabel(i));
                    }
                    while (rs.next()) {
                        if (cap.rows.size() >= MAX_ROWS) {
                            cap.truncated = true;
                            break;
                        }
                        List<String> row = new ArrayList<>(n);
                        for (int i = 1; i <= n; i++) {
                            row.add(normalize(rs.getObject(i)));
                        }
                        cap.rows.add(row);
                    }
                }
                last = cap;
            } else {
                int updated = st.getUpdateCount();
                if (updated == -1) {
                    return last;
                }
                Captured cap = new Captured();
                cap.updateCount = updated;
                last = cap;
            }
            isResultSet = st.getMoreResults();
        }
    }

    private void evaluate(String p, Result r, Captured last, Exception error) throws IOException {
        String expectError = c.get(p + ".expect.error");
        if (expectError != null && !expectError.isEmpty()) {
            if (error == null) {
                r.failures.add("expected an error, but the statements succeeded");
            } else if (!expectError.equalsIgnoreCase("true")
                    && !lower(Util.describe(error)).contains(lower(expectError))) {
                r.failures.add("expected an error containing '" + expectError + "', got: " + Util.describe(error));
            }
        } else if (error != null) {
            r.failures.add("error: " + Util.describe(error));
        } else {
            String rows = c.get(p + ".expect.rows");
            String value = c.get(p + ".expect.value");
            String updateCount = c.get(p + ".expect.updateCount");
            Path csv = c.getPath(p + ".expect.csv");
            boolean needsResultSet = rows != null || value != null || csv != null;
            if (needsResultSet && (last == null || !last.isResultSet())) {
                r.failures.add("expected a result set, but the last statement returned none");
            } else {
                if (last != null && last.truncated) {
                    r.failures.add("result has more than " + MAX_ROWS + " rows; only the first " + MAX_ROWS + " were compared");
                }
                if (rows != null && last.rows.size() != Long.parseLong(rows)) {
                    r.failures.add("expected " + rows + " row(s), got " + last.rows.size());
                }
                if (value != null) {
                    String actual = last.rows.isEmpty() || last.rows.get(0).isEmpty() ? null : last.rows.get(0).get(0);
                    String expected = value.equals("NULL") ? null : value;
                    if (last.rows.isEmpty()) {
                        r.failures.add("expected value '" + value + "', got no rows");
                    } else if (!canon(expected).equals(canon(actual))) {
                        r.failures.add("expected value '" + value + "', got '" + actual + "'");
                    }
                }
                if (csv != null) {
                    compareCsv(csv, last, c.getBool(p + ".expect.ordered", false), r);
                }
            }
            if (updateCount != null) {
                if (last == null || last.isResultSet()) {
                    r.failures.add("expected an update count, but the last statement returned a result set");
                } else if (last.updateCount != Long.parseLong(updateCount)) {
                    r.failures.add("expected update count " + updateCount + ", got " + last.updateCount);
                }
            }
        }
        long maxMs = c.getLong(p + ".expect.maxMs", 0);
        if (maxMs > 0 && r.elapsedMicros > maxMs * 1000) {
            r.failures.add("took " + Util.ms(r.elapsedMicros) + " ms, limit is " + maxMs + " ms");
        }
    }

    private static void compareCsv(Path file, Captured actual, boolean ordered, Result r) throws IOException {
        if (!Files.isRegularFile(file)) {
            r.failures.add("expected CSV not found: " + file);
            return;
        }
        List<List<String>> data = Csv.read(file);
        if (data.isEmpty()) {
            r.failures.add("expected CSV is empty (it needs a header row): " + file);
            return;
        }
        List<String> header = data.get(0);
        List<List<String>> expected = data.subList(1, data.size());
        if (header.size() != actual.columns.size()) {
            r.failures.add("expected " + header.size() + " column(s) " + header + ", got " + actual.columns.size()
                    + " " + actual.columns);
            return;
        }
        for (int i = 0; i < header.size(); i++) {
            if (header.get(i) == null || !header.get(i).equalsIgnoreCase(actual.columns.get(i))) {
                r.failures.add("column " + (i + 1) + ": expected '" + header.get(i) + "', got '" + actual.columns.get(i) + "'");
            }
        }
        if (expected.size() != actual.rows.size()) {
            r.failures.add("expected " + expected.size() + " row(s), got " + actual.rows.size());
        }
        if (ordered) {
            int diffs = 0;
            for (int i = 0; i < Math.max(expected.size(), actual.rows.size()) && diffs < MAX_DIFFS; i++) {
                List<String> e = i < expected.size() ? expected.get(i) : null;
                List<String> a = i < actual.rows.size() ? actual.rows.get(i) : null;
                if (e == null || a == null || !key(e).equals(key(a))) {
                    r.failures.add("row " + (i + 1) + ": expected " + show(e) + ", got " + show(a));
                    diffs++;
                }
            }
        } else {
            Map<String, List<String>> shown = new LinkedHashMap<>();
            Map<String, Integer> balance = new LinkedHashMap<>();
            for (List<String> e : expected) {
                shown.putIfAbsent(key(e), e);
                balance.merge(key(e), 1, Integer::sum);
            }
            for (List<String> a : actual.rows) {
                shown.putIfAbsent(key(a), a);
                balance.merge(key(a), -1, Integer::sum);
            }
            int missing = 0;
            int unexpected = 0;
            for (Map.Entry<String, Integer> e : balance.entrySet()) {
                int n = e.getValue();
                if (n > 0 && missing++ < MAX_DIFFS) {
                    r.failures.add("missing row" + (n > 1 ? " (x" + n + ")" : "") + ": " + show(shown.get(e.getKey())));
                } else if (n < 0 && unexpected++ < MAX_DIFFS) {
                    r.failures.add("unexpected row" + (n < -1 ? " (x" + -n + ")" : "") + ": " + show(shown.get(e.getKey())));
                }
            }
        }
    }

    private static String key(List<String> row) {
        StringBuilder sb = new StringBuilder();
        for (String v : row) {
            sb.append(canon(v)).append('\u001f');
        }
        return sb.toString();
    }

    private static String show(List<String> row) {
        return row == null ? "(no row)" : Arrays.toString(row.toArray());
    }

    /** Canonical form for comparison: numbers compare by value (1.50 == 1.5), NULL is distinct from "". */
    private static String canon(String v) {
        if (v == null) {
            return "\u0000NULL";
        }
        String t = v.trim();
        if (NUMBER.matcher(t).matches()) {
            BigDecimal d = new BigDecimal(t).stripTrailingZeros();
            return d.signum() == 0 ? "0" : d.toPlainString();
        }
        return v;
    }

    private static String normalize(Object o) throws SQLException {
        if (o == null) {
            return null;
        }
        if (o instanceof Float) {
            return new BigDecimal(o.toString()).toPlainString();
        }
        if (o instanceof Double) {
            double d = (Double) o;
            return Double.isNaN(d) || Double.isInfinite(d) ? o.toString() : BigDecimal.valueOf(d).toPlainString();
        }
        if (o instanceof BigDecimal) {
            return ((BigDecimal) o).toPlainString();
        }
        if (o instanceof Timestamp) {
            String s = o.toString();
            return s.endsWith(".0") ? s.substring(0, s.length() - 2) : s;
        }
        if (o instanceof byte[]) {
            StringBuilder sb = new StringBuilder();
            for (byte b : (byte[]) o) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        }
        if (o instanceof Clob) {
            try (Reader r = ((Clob) o).getCharacterStream()) {
                StringBuilder sb = new StringBuilder();
                char[] buf = new char[8192];
                for (int n; (n = r.read(buf)) > 0; ) {
                    sb.append(buf, 0, n);
                }
                return sb.toString();
            } catch (IOException e) {
                throw new SQLException("reading CLOB failed", e);
            }
        }
        return o.toString();
    }

    private static String lower(String s) {
        return s.toLowerCase(Locale.ROOT);
    }

    static void writeJUnit(List<Result> results, Path file) throws IOException {
        long failures = results.stream().filter(r -> !r.passed()).count();
        double total = results.stream().mapToLong(r -> r.elapsedMicros).sum() / 1e6;
        StringBuilder sb = new StringBuilder("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
        sb.append(String.format(Locale.ROOT, "<testsuite name=\"dbmeter\" tests=\"%d\" failures=\"%d\" errors=\"0\" time=\"%.3f\">%n",
                results.size(), failures, total));
        for (Result r : results) {
            sb.append(String.format(Locale.ROOT, "  <testcase classname=\"dbmeter.check\" name=\"%s\" time=\"%.3f\"",
                    xml(r.name), r.elapsedMicros / 1e6));
            if (r.passed()) {
                sb.append("/>\n");
            } else {
                String text = String.join("\n", r.failures);
                sb.append(">\n    <failure message=\"").append(xml(r.failures.get(0))).append("\">")
                        .append(xml(text)).append("</failure>\n  </testcase>\n");
            }
        }
        sb.append("</testsuite>\n");
        Path parent = file.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        Util.writeFile(file, sb.toString());
    }

    private static String xml(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }
}
