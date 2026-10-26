package dbmeter;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public final class Main {
    private static final String USAGE = String.join("\n",
            "dbmeter - JDBC database load and correctness tester",
            "",
            "Usage:",
            "  dbmeter info  -c <config>             connect, print driver/database info, run connection.testQuery",
            "  dbmeter check -c <config>             run correctness checks (check.<name>.*)",
            "  dbmeter load  -c <config>             run a load test (scenario.<name>.*, load.*)",
            "  dbmeter merge <report.json|dir>...    merge node reports of a distributed run",
            "  dbmeter compare <report.json|dir>...  side-by-side metrics of runs (e.g. one per database)",
            "",
            "Options:",
            "  -c, --config <file>    configuration file (.properties)",
            "  --set key=value        override a config property (repeatable)",
            "  --only a,b,...         run only these scenarios / checks",
            "  --node <name>          node name used in reports (default: run.node or host name)",
            "  --run-id <id>          run id shared by all nodes of one run (default: run.id or timestamp)",
            "  --start-at <time>      synchronized start: 2026-10-23T14:00:00Z, local 14:00[:30], or +30s",
            "  --out <dir>            report directory (default: reports/<run-id>)",
            "  --skip-setup           don't run load.setup/teardown or check.setup/teardown scripts",
            "  --junit <file>         (check) also write JUnit XML results",
            "",
            "Set -Ddbmeter.debug=true for stack traces.");

    private Main() {
    }

    public static void main(String[] args) {
        int code;
        try {
            code = run(args);
        } catch (UsageException e) {
            System.err.println("error: " + e.getMessage());
            System.err.println();
            System.err.println(USAGE);
            code = 2;
        } catch (Exception e) {
            System.err.println("error: " + Util.describe(e));
            if (Boolean.getBoolean("dbmeter.debug")) {
                e.printStackTrace();
            }
            code = 1;
        }
        System.exit(code);
    }

    static int run(String[] args) throws Exception {
        if (args.length == 0 || args[0].equals("-h") || args[0].equals("--help") || args[0].equals("help")) {
            System.out.println(USAGE);
            return 0;
        }
        Options o = Options.parse(Arrays.copyOfRange(args, 1, args.length));
        switch (args[0]) {
            case "info":
                return info(o);
            case "check":
                return check(o);
            case "load":
                return load(o);
            case "merge":
                return merge(o);
            case "compare":
                return compare(o);
            default:
                throw new UsageException("unknown command '" + args[0] + "'");
        }
    }

    private static int info(Options o) throws Exception {
        Config c = o.loadConfig();
        Db db = Db.fromConfig(c);
        System.out.println("URL:            " + Util.maskUrl(db.url));
        System.out.println("Driver class:   " + db.driver.getClass().getName());
        long t0 = System.nanoTime();
        try (Connection con = db.connect()) {
            long connectMicros = (System.nanoTime() - t0) / 1000;
            for (Map.Entry<String, Object> e : Db.describe(con).entrySet()) {
                System.out.printf("%-16s%s%n", e.getKey() + ":", e.getValue());
            }
            System.out.printf("%-16s%s ms%n", "Connect time:", Util.ms(connectMicros));
            System.out.printf("%-16s%s%n", "Auto-commit:", con.getAutoCommit());
            String isolation;
            try {
                isolation = Db.isolationName(con.getTransactionIsolation());
            } catch (SQLException e) {
                isolation = "unknown (" + Util.describe(e) + ")";
            }
            System.out.printf("%-16s%s%n", "Isolation:", isolation);
            String testQuery = c.get("connection.testQuery");
            if (testQuery != null && !testQuery.isEmpty()) {
                long q0 = System.nanoTime();
                try (Statement st = con.createStatement(); ResultSet rs = st.executeQuery(testQuery)) {
                    String value = rs.next() ? String.valueOf(rs.getObject(1)) : "(no rows)";
                    System.out.printf("%-16s%s -> %s (%s ms)%n", "Test query:", testQuery, value,
                            Util.ms((System.nanoTime() - q0) / 1000));
                }
            }
        }
        System.out.println("OK");
        return 0;
    }

    private static int check(Options o) throws Exception {
        Config c = o.loadConfig();
        Db db = Db.fromConfig(c);
        List<CheckRunner.Result> results = new CheckRunner(c, db).run(o.only, o.skipSetup);
        long failed = results.stream().filter(r -> !r.passed()).count();
        System.out.println();
        System.out.printf("%d check(s): %d passed, %d failed%n", results.size(), results.size() - failed, failed);
        if (o.junit != null) {
            CheckRunner.writeJUnit(results, o.junit);
            System.out.println("JUnit report: " + o.junit.toAbsolutePath());
        }
        return failed == 0 ? 0 : 1;
    }

    private static int load(Options o) throws Exception {
        Config c = o.loadConfig();
        Db db = Db.fromConfig(c);
        List<Scenario> scenarios = Scenario.fromConfig(c, o.only);
        String runId = o.runId != null ? o.runId
                : c.get("run.id", LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss")));
        String node = o.node != null ? o.node : c.get("run.node", Util.hostName());
        Path out = o.out != null ? o.out
                : (c.get("run.out") != null ? c.resolve(c.get("run.out")) : Paths.get("reports")).resolve(runId);
        String startAt = o.startAt != null ? o.startAt : c.get("load.startAt");
        long startAtMs = startAt != null && !startAt.isEmpty() ? parseStartAt(startAt) : System.currentTimeMillis();

        System.out.printf("run %s, node %s, scenarios: %s%n", runId, node,
                scenarios.stream().map(s -> s.name + "(w" + s.weight + ")").collect(Collectors.joining(", ")));
        if (!o.skipSetup) {
            try (Connection con = db.connect()) {
                Scripts.run(con, c, "load.setup");
            }
        }
        LoadRunner runner = new LoadRunner(c, db, scenarios, runId, node, startAtMs);
        try {
            RunResult result = runner.run();
            Path json = result.writeFiles(out, Util.safeFileName(node));
            System.out.println();
            System.out.print(result.summary());
            System.out.println();
            System.out.println("Report: " + json.toAbsolutePath());
        } finally {
            runner.complete();
        }
        if (!o.skipSetup) {
            try (Connection con = db.connect()) {
                Scripts.run(con, c, "load.teardown");
            }
        }
        return 0;
    }

    /** A directory argument stands for its merged.json if present, else its single node report. */
    private static int compare(Options o) throws IOException {
        List<RunResult> runs = new ArrayList<>();
        for (String arg : o.positional) {
            Path p = Paths.get(arg);
            if (Files.isDirectory(p)) {
                Path merged = p.resolve("merged.json");
                if (Files.isRegularFile(merged)) {
                    p = merged;
                } else {
                    try (Stream<Path> s = Files.list(p)) {
                        List<Path> jsons = s.filter(f -> f.getFileName().toString().endsWith(".json")).collect(Collectors.toList());
                        if (jsons.size() != 1) {
                            throw new UsageException(p + " has " + jsons.size() + " reports; merge them first or name one");
                        }
                        p = jsons.get(0);
                    }
                }
            } else if (!Files.isRegularFile(p)) {
                throw new UsageException("not found: " + p);
            }
            runs.add(RunResult.fromJson(Json.parse(Util.readFile(p))));
        }
        if (runs.size() < 2) {
            throw new UsageException("compare needs at least two reports");
        }
        String table = Compare.table(runs);
        System.out.print(table);
        if (o.out != null) {
            Files.createDirectories(o.out);
            Util.writeFile(o.out.resolve("compare.txt"), table);
            Util.writeFile(o.out.resolve("compare.csv"), Compare.csv(runs));
            System.out.println();
            System.out.println("Written: " + o.out.resolve("compare.txt").toAbsolutePath() + " and compare.csv");
        }
        return 0;
    }

    private static int merge(Options o) throws IOException {
        List<Path> files = new ArrayList<>();
        for (String arg : o.positional) {
            Path p = Paths.get(arg);
            if (Files.isDirectory(p)) {
                try (Stream<Path> s = Files.list(p)) {
                    s.filter(f -> f.getFileName().toString().endsWith(".json"))
                            .filter(f -> !f.getFileName().toString().startsWith("merged"))
                            .sorted()
                            .forEach(files::add);
                }
            } else if (Files.isRegularFile(p)) {
                files.add(p);
            } else {
                throw new UsageException("not found: " + p);
            }
        }
        if (files.isEmpty()) {
            throw new UsageException("merge needs report files or a directory containing them");
        }
        RunResult merged = null;
        for (Path f : files) {
            RunResult r = RunResult.fromJson(Json.parse(Util.readFile(f)));
            System.out.println("read " + f);
            if (merged == null) {
                merged = r;
            } else {
                if (!Objects.equals(merged.runId, r.runId)) {
                    System.out.printf("warning: %s has run id '%s', expected '%s'%n", f, r.runId, merged.runId);
                }
                merged.merge(r);
            }
        }
        Path out = o.out != null ? o.out : files.get(0).toAbsolutePath().getParent();
        Path json = merged.writeFiles(out, "merged");
        System.out.println();
        System.out.print(merged.summary());
        System.out.println();
        System.out.println("Merged report: " + json.toAbsolutePath());
        return 0;
    }

    static long parseStartAt(String s) {
        s = s.trim();
        if (s.startsWith("+")) {
            return System.currentTimeMillis() + Config.parseDuration(s.substring(1), "--start-at").toMillis();
        }
        try {
            return Instant.parse(s).toEpochMilli();
        } catch (DateTimeParseException ignored) {
            // try the next format
        }
        try {
            return OffsetDateTime.parse(s).toInstant().toEpochMilli();
        } catch (DateTimeParseException ignored) {
            // try the next format
        }
        try {
            return LocalDateTime.parse(s).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
        } catch (DateTimeParseException ignored) {
            // try the next format
        }
        try {
            ZonedDateTime at = ZonedDateTime.now().with(LocalTime.parse(s));
            if (at.isBefore(ZonedDateTime.now())) {
                at = at.plusDays(1);
            }
            return at.toInstant().toEpochMilli();
        } catch (DateTimeParseException ignored) {
            throw new UsageException("invalid start time '" + s + "'");
        }
    }

    static final class Options {
        Path config;
        final Map<String, String> overrides = new LinkedHashMap<>();
        final Set<String> only = new LinkedHashSet<>();
        final List<String> positional = new ArrayList<>();
        String node;
        String runId;
        String startAt;
        Path out;
        Path junit;
        boolean skipSetup;

        static Options parse(String[] a) {
            Options o = new Options();
            for (int i = 0; i < a.length; i++) {
                String arg = a[i];
                switch (arg) {
                    case "-c":
                    case "--config":
                        o.config = Paths.get(value(a, ++i, arg));
                        break;
                    case "--set": {
                        String kv = value(a, ++i, arg);
                        int eq = kv.indexOf('=');
                        if (eq <= 0) {
                            throw new UsageException("--set expects key=value, got '" + kv + "'");
                        }
                        o.overrides.put(kv.substring(0, eq).trim(), kv.substring(eq + 1));
                        break;
                    }
                    case "--only":
                        for (String n : value(a, ++i, arg).split(",")) {
                            if (!n.trim().isEmpty()) {
                                o.only.add(n.trim());
                            }
                        }
                        break;
                    case "--node":
                        o.node = value(a, ++i, arg);
                        break;
                    case "--run-id":
                        o.runId = value(a, ++i, arg);
                        break;
                    case "--start-at":
                        o.startAt = value(a, ++i, arg);
                        break;
                    case "--out":
                        o.out = Paths.get(value(a, ++i, arg));
                        break;
                    case "--junit":
                        o.junit = Paths.get(value(a, ++i, arg));
                        break;
                    case "--skip-setup":
                        o.skipSetup = true;
                        break;
                    default:
                        if (arg.startsWith("-")) {
                            throw new UsageException("unknown option '" + arg + "'");
                        }
                        o.positional.add(arg);
                }
            }
            return o;
        }

        Config loadConfig() throws IOException {
            if (config == null) {
                throw new UsageException("missing -c <config file>");
            }
            if (!Files.isRegularFile(config)) {
                throw new UsageException("config file not found: " + config.toAbsolutePath());
            }
            return Config.load(config, overrides);
        }

        private static String value(String[] a, int i, String option) {
            if (i >= a.length) {
                throw new UsageException(option + " requires a value");
            }
            return a[i];
        }
    }

    static final class UsageException extends RuntimeException {
        UsageException(String message) {
            super(message);
        }
    }
}
