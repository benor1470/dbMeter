package dbmeter;

import java.io.IOException;
import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.List;

/** Runs setup/teardown scripts ({@code <prefix>.file} or {@code <prefix>.sql}) statement by statement. */
final class Scripts {
    private static final int MAX_WARNINGS = 3;

    private Scripts() {
    }

    static void run(Connection con, Config c, String prefix) throws IOException, SQLException {
        List<String> stmts = SqlScript.loadOptional(c, prefix);
        if (stmts == null) {
            return;
        }
        boolean continueOnError = c.getBool(prefix + ".continueOnError", false);
        int failed = 0;
        long t0 = System.nanoTime();
        try (Statement st = con.createStatement()) {
            for (int i = 0; i < stmts.size(); i++) {
                try {
                    st.execute(stmts.get(i));
                } catch (SQLException e) {
                    String msg = prefix + " statement " + (i + 1) + " failed: " + Util.describe(e);
                    if (!continueOnError) {
                        throw new SQLException(msg + "\n  SQL: " + Util.truncate(stmts.get(i), 200), e.getSQLState(), e);
                    }
                    if (++failed <= MAX_WARNINGS) {
                        System.out.println("warning: " + msg);
                    } else if (failed == MAX_WARNINGS + 1) {
                        System.out.println("warning: further failures of " + prefix + " are counted, not printed");
                    }
                }
            }
        }
        if (!con.getAutoCommit()) {
            con.commit();
        }
        System.out.printf("%s: %d statement(s)%s in %s ms%n", prefix, stmts.size(),
                failed > 0 ? ", " + failed + " failed" : "", Util.ms((System.nanoTime() - t0) / 1000));
    }
}
