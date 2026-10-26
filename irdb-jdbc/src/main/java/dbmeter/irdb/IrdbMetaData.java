package dbmeter.irdb;

import java.sql.Connection;

/** Backs java.sql.DatabaseMetaData with the few facts the adapter knows. */
public final class IrdbMetaData {
    private final IrdbConnection con;

    IrdbMetaData(IrdbConnection con) {
        this.con = con;
    }

    public String getDatabaseProductName() {
        return "InMemory.Net IRDB";
    }

    public String getDatabaseProductVersion() {
        return "server " + con.url.host + ":" + con.url.port + ", database " + con.url.database;
    }

    public String getDriverName() {
        return "dbmeter IRDB adapter (jirdb)";
    }

    public String getDriverVersion() {
        return IrdbDriver.VERSION;
    }

    public int getDriverMajorVersion() {
        return 0;
    }

    public int getDriverMinorVersion() {
        return 1;
    }

    public int getJDBCMajorVersion() {
        return 4;
    }

    public int getJDBCMinorVersion() {
        return 2;
    }

    public String getURL() {
        return con.url.url;
    }

    public String getUserName() {
        return con.url.user;
    }

    public boolean supportsTransactions() {
        return false;
    }

    public int getDefaultTransactionIsolation() {
        return Connection.TRANSACTION_NONE;
    }

    public boolean isReadOnly() {
        return true;
    }
}
