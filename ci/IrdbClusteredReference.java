import irdb.IPClient;
import irdb.InMemoryTable;

/** Prepares the vendor clustered index outside the timed JDBC workload. */
class IrdbClusteredReference {
    private static void require(boolean condition, IPClient client, String operation) {
        if (!condition) {
            throw new IllegalStateException(operation + ": " + client.lastError);
        }
    }

    private static void verifyRows(InMemoryTable result, IPClient client, String operation) {
        require(result != null && result.rowCount == 1, client, operation);
        Object[] row = result.getRow(0);
        require(((Number) row[0]).longValue() == 1_000_000
                && ((Number) row[1]).longValue() == 1
                && ((Number) row[2]).longValue() == 1_000_000, client, operation);
    }

    public static void main(String[] args) throws Exception {
        String password = System.getenv().getOrDefault("IRDB_PASSWORD", "!TopSecret");
        IPClient client = new IPClient("127.0.0.1", Integer.parseInt(args[0]), "default", password, true, 30, false);
        String temporary = null;
        String counts = "SELECT COUNT(*) AS n, MIN(id) AS lo, MAX(id) AS hi FROM t_1000000";
        try {
            require(client.isConnected(), client, "connect");
            temporary = client.createTempDB();
            require(temporary != null, client, "create temporary database");
            require(client.loadDBtoTempDB(args[1], temporary), client, "load original database into temporary database");
            String index = "CREATE CLUSTERED INDEX ON t_1000000 (id)";
            verifyRows(client.executeTempDB(temporary, index + ";\n" + counts), client, "build clustered index and verify data");
            require(client.saveTempDB(temporary, args[2], true), client, "save clustered reference database");
            temporary = null; // saveTempDB with delete=true takes ownership.
            require(client.reload(args[2]) || client.load(args[2]), client, "load stored clustered database");
            verifyRows(client.execute(args[2], counts), client, "verify the stored clustered data");
            System.out.println(index);
            System.out.println("Clustered reference saved and reloaded with 1000000 rows and id range 1..1000000");
        } finally {
            if (temporary != null) {
                client.removeTempDB(temporary);
            }
            client.close();
        }
    }
}
