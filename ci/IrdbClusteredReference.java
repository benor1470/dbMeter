import irdb.IPClient;
import irdb.InMemoryTable;

/** Prepares the vendor clustered index outside the timed JDBC workload. */
class IrdbClusteredReference {
    private static void require(boolean condition, IPClient client, String operation) {
        if (!condition) {
            throw new IllegalStateException(operation + ": " + client.lastError);
        }
    }

    private static void verifyRows(InMemoryTable result, IPClient client, String operation, long expected) {
        require(result != null && result.rowCount == 1, client, operation);
        Object[] row = result.getRow(0);
        require(((Number) row[0]).longValue() == expected
                && ((Number) row[1]).longValue() == 1
                && ((Number) row[2]).longValue() == expected, client, operation);
    }

    public static void main(String[] args) throws Exception {
        String password = System.getenv().getOrDefault("IRDB_PASSWORD", "!TopSecret");
        IPClient client = new IPClient("127.0.0.1", Integer.parseInt(args[0]), "default", password, true, 120, false);
        String temporary = null;
        String[] definitions = (args.length == 4 ? args[3] : "t_1000000:1000000").split(",");
        String[] tables = new String[definitions.length];
        long[] expected = new long[definitions.length];
        for (int i = 0; i < definitions.length; i++) {
            String[] definition = definitions[i].split(":", -1);
            if (definition.length != 2 || !definition[0].matches("[a-z][a-z0-9_]*"))
                throw new IllegalArgumentException("Invalid original table definition");
            tables[i] = definition[0];
            expected[i] = Long.parseLong(definition[1]);
            if (expected[i] <= 0) throw new IllegalArgumentException("Expected positive original row count");
        }
        try {
            require(client.isConnected(), client, "connect");
            temporary = client.createTempDB();
            require(temporary != null, client, "create temporary database");
            require(client.loadDBtoTempDB(args[1], temporary), client, "load original database into temporary database");
            for (int i = 0; i < tables.length; i++) {
                String counts = "SELECT COUNT(*) AS n, MIN(id) AS lo, MAX(id) AS hi FROM " + tables[i];
                verifyRows(client.executeTempDB(temporary, counts), client, "verify original " + tables[i], expected[i]);
                String index = "CREATE CLUSTERED INDEX ON " + tables[i] + " (id)";
                verifyRows(client.executeTempDB(temporary, index + ";\n" + counts), client, "build index " + tables[i], expected[i]);
                System.out.println(index);
            }
            require(client.saveTempDB(temporary, args[2], true), client, "save clustered reference database");
            temporary = null; // saveTempDB with delete=true takes ownership.
            require(client.reload(args[2]) || client.load(args[2]), client, "load stored clustered database");
            for (int i = 0; i < tables.length; i++) {
                String counts = "SELECT COUNT(*) AS n, MIN(id) AS lo, MAX(id) AS hi FROM " + tables[i];
                verifyRows(client.execute(args[2], counts), client, "verify stored " + tables[i], expected[i]);
                System.out.println(tables[i] + " saved and reloaded with " + expected[i] + " rows and id range 1.." + expected[i]);
            }
        } finally {
            if (temporary != null) {
                client.removeTempDB(temporary);
            }
            client.close();
        }
    }
}
