package dbmeter.irdb;

import irdb.IPClient;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;

/**
 * Copies tables (or query results) from any JDBC source into an IRDB database, replacing it.
 *
 * <pre>
 * java -cp irdb-jdbc.jar;jirdb.jar;fastutil.jar;&lt;source driver&gt;.jar dbmeter.irdb.CopyToIrdb
 *     --source jdbc:postgresql://localhost/postgres --source-user postgres --source-password 1
 *     --source-schema dbmeter_agg --target jdbc:irdb://localhost:5060/sales_agg
 *     --tables regions,stores,products,customers,sales [--query name=SELECT ...] [--chunk 500000]
 * </pre>
 * The target accepts the same URL and user/password properties as the JDBC driver
 * (--target-user, --target-password).
 */
public final class CopyToIrdb {
    private CopyToIrdb() {
    }

    public static void main(String[] args) throws Exception {
        Map<String, String> o = new LinkedHashMap<>();
        Map<String, String> sources = new LinkedHashMap<>();
        for (int i = 0; i < args.length; i++) {
            String a = args[i];
            if (!a.startsWith("--") || i + 1 >= args.length) {
                usage("unexpected argument " + a);
            }
            String v = args[++i];
            if (a.equals("--query")) {
                int eq = v.indexOf('=');
                if (eq <= 0) {
                    usage("--query expects name=SQL");
                }
                sources.put(v.substring(0, eq), v.substring(eq + 1));
            } else {
                o.put(a.substring(2), v);
            }
        }
        if (!o.containsKey("source") || !o.containsKey("target")) {
            usage("--source and --target are required");
        }
        String schema = o.get("source-schema");
        if (o.containsKey("tables")) {
            for (String t : o.get("tables").split(",")) {
                String name = t.trim();
                if (!name.isEmpty()) {
                    sources.put(name, "SELECT * FROM " + (schema == null ? "" : schema + ".") + name);
                }
            }
        }
        if (sources.isEmpty()) {
            usage("nothing to copy: give --tables and/or --query");
        }
        int chunk = Integer.parseInt(o.getOrDefault("chunk", "500000"));

        Properties targetProps = new Properties();
        if (o.containsKey("target-user")) {
            targetProps.setProperty("user", o.get("target-user"));
        }
        if (o.containsKey("target-password")) {
            targetProps.setProperty("password", o.get("target-password"));
        }
        IrdbUrl target = IrdbUrl.parse(o.get("target"), targetProps);

        Properties sourceProps = new Properties();
        if (o.containsKey("source-user")) {
            sourceProps.setProperty("user", o.get("source-user"));
        }
        if (o.containsKey("source-password")) {
            sourceProps.setProperty("password", o.get("source-password"));
        }

        long t0 = System.nanoTime();
        IPClient client = new IPClient(target.host, target.port, target.user, target.password,
                target.timeoutSeconds > 0, target.timeoutSeconds, target.encrypt);
        if (!client.isConnected()) {
            fail("cannot connect to IRDB: " + IrdbConnection.firstNonEmpty(client.connectionException, client.lastError));
        }
        String temp = null;
        try (Connection src = DriverManager.getConnection(o.get("source"), sourceProps)) {
            // Lets drivers such as PostgreSQL stream rows instead of buffering whole tables.
            src.setAutoCommit(false);
            temp = client.createTempDB();
            List<String> copied = new ArrayList<>();
            for (Map.Entry<String, String> e : sources.entrySet()) {
                long s0 = System.nanoTime();
                try (Statement st = src.createStatement()) {
                    st.setFetchSize(Math.min(chunk, 50_000));
                    try (ResultSet rs = st.executeQuery(e.getValue())) {
                        if (!client.addTableInChunksToTempDB(temp, e.getKey(), rs, chunk)) {
                            fail("adding table " + e.getKey() + " failed: " + client.lastError);
                        }
                    }
                }
                if (!client.columnizeTableInTempDB(temp, e.getKey())) {
                    fail("columnizing table " + e.getKey() + " failed: " + client.lastError);
                }
                System.out.printf(Locale.ROOT, "copied %-20s in %.1f s%n", e.getKey(), (System.nanoTime() - s0) / 1e9);
                copied.add(e.getKey());
            }
            src.rollback();
            if (!client.saveTempDB(temp, target.database, true)) {
                fail("saving database " + target.database + " failed: " + client.lastError);
            }
            temp = null;
            if (!client.reload(target.database) && !client.load(target.database)) {
                fail("loading database " + target.database + " failed: " + client.lastError);
            }
            System.out.printf(Locale.ROOT, "IRDB database '%s' ready with %s (%.1f s total)%n", target.database, copied,
                    (System.nanoTime() - t0) / 1e9);
        } catch (SQLException e) {
            fail("source error: " + e.getMessage());
        } finally {
            if (temp != null) {
                client.removeTempDB(temp);
            }
            client.close();
        }
    }

    private static void usage(String msg) {
        fail(msg + "\nusage: CopyToIrdb --source <jdbc url> [--source-user u] [--source-password p] [--source-schema s]"
                + "\n                  --target jdbc:irdb://host:port/db [--target-user u] [--target-password p]"
                + "\n                  [--tables a,b,c] [--query name=SQL]... [--chunk rows]");
    }

    private static void fail(String msg) {
        System.err.println("error: " + msg);
        System.exit(1);
    }
}
