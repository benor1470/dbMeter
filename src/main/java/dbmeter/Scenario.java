package dbmeter;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * One unit of load work: the statements of a SQL file executed in order (in one transaction when there are
 * several), timed as a whole. Scenarios are picked at random in proportion to their weight.
 */
final class Scenario {
    final String name;
    final List<BoundSql> statements;
    final int weight;
    final boolean transaction;
    final boolean rollback;
    final ParamSource params;
    final long thinkTimeMs;

    private Scenario(String name, List<BoundSql> statements, int weight, boolean transaction, boolean rollback,
                     ParamSource params, long thinkTimeMs) {
        this.name = name;
        this.statements = statements;
        this.weight = weight;
        this.transaction = transaction;
        this.rollback = rollback;
        this.params = params;
        this.thinkTimeMs = thinkTimeMs;
    }

    static List<Scenario> fromConfig(Config c, Set<String> only) throws IOException {
        Set<String> wanted = new LinkedHashSet<>(only);
        if (wanted.isEmpty() && c.get("load.scenarios") != null) {
            for (String n : c.get("load.scenarios").split(",")) {
                if (!n.trim().isEmpty()) {
                    wanted.add(n.trim());
                }
            }
        }
        Set<String> defined = c.groupNames("scenario.");
        for (String n : wanted) {
            if (!defined.contains(n)) {
                throw new ConfigException("unknown scenario '" + n + "' (defined: " + defined + ")");
            }
        }
        List<Scenario> list = new ArrayList<>();
        for (String name : defined) {
            String p = "scenario." + name;
            if (!wanted.isEmpty() && !wanted.contains(name)) {
                continue;
            }
            int weight = c.getInt(p + ".weight", 1);
            if (!c.getBool(p + ".enabled", true) || weight <= 0) {
                continue;
            }
            List<BoundSql> statements = new ArrayList<>();
            for (String s : SqlScript.load(c, p)) {
                statements.add(BoundSql.parse(s));
            }
            boolean rollback = c.getBool(p + ".rollback", false);
            boolean tx = rollback || c.getBool(p + ".transaction", statements.size() > 1);
            ParamSource params = null;
            if (c.getPath(p + ".params.file") != null) {
                params = ParamSource.load(c.getPath(p + ".params.file"), c.get(p + ".params.mode", "sequential"));
            }
            for (BoundSql b : statements) {
                for (String col : b.columns()) {
                    if (params == null) {
                        throw new ConfigException(p + ": placeholder :{" + col + "} needs " + p + ".params.file");
                    }
                    if (!params.hasColumn(col)) {
                        throw new ConfigException(p + ": column '" + col + "' not in " + params.file + " " + params.columns);
                    }
                }
            }
            long think = c.getDuration(p + ".thinkTime", java.time.Duration.ZERO).toMillis();
            list.add(new Scenario(name, statements, weight, tx, rollback, params, think));
        }
        if (list.isEmpty()) {
            throw new ConfigException("no scenarios to run - define scenario.<name>.file or scenario.<name>.sql");
        }
        return list;
    }
}
