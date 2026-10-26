package dbmeter.irdb;

import irdb.IPClient;

import java.io.IOException;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.SQLWarning;
import java.sql.Statement;

/** Backs java.sql.Connection: one IPClient session with the database loaded. Public methods are the JDBC API. */
public final class IrdbConnection {
    final IrdbUrl url;
    final IPClient client;
    private boolean closed;
    private boolean autoCommit = true;

    IrdbConnection(IrdbUrl url) throws SQLException {
        this.url = url;
        client = new IPClient(url.host, url.port, url.user, url.password, url.timeoutSeconds > 0, url.timeoutSeconds,
                url.encrypt);
        if (!client.isConnected()) {
            throw new SQLException("cannot connect to IRDB at " + url.host + ":" + url.port + ": "
                    + firstNonEmpty(client.connectionException, client.lastError, "unknown error"), "08001");
        }
        try {
            if (!client.load(url.database)) {
                throw error("cannot load IRDB database '" + url.database + "'", "3D000");
            }
        } catch (IOException e) {
            client.forceClose();
            throw new SQLException("loading IRDB database '" + url.database + "' failed: " + e.getMessage(), "08006", e);
        } catch (SQLException e) {
            client.forceClose();
            throw e;
        }
    }

    SQLException error(String what, String state) {
        return new SQLException(what + ": " + firstNonEmpty(client.lastError, "no details"), state);
    }

    static String firstNonEmpty(String... values) {
        for (String v : values) {
            if (v != null && !v.trim().isEmpty()) {
                return v.trim();
            }
        }
        return "";
    }

    void checkOpen() throws SQLException {
        if (closed) {
            throw new SQLException("connection is closed", "08003");
        }
    }

    public Statement createStatement() throws SQLException {
        checkOpen();
        return Jdbc.proxy(Statement.class, new IrdbStatement(this, null));
    }

    public PreparedStatement prepareStatement(String sql) throws SQLException {
        checkOpen();
        return Jdbc.proxy(PreparedStatement.class, new IrdbStatement(this, sql));
    }

    public DatabaseMetaData getMetaData() {
        return Jdbc.proxy(DatabaseMetaData.class, new IrdbMetaData(this));
    }

    public void close() {
        if (!closed) {
            closed = true;
            try {
                client.close();
            } catch (IOException e) {
                client.forceClose();
            }
        }
    }

    public boolean isClosed() {
        return closed;
    }

    public boolean isValid(int timeout) {
        try {
            return !closed && client.noop();
        } catch (IOException e) {
            return false;
        }
    }

    public boolean getAutoCommit() {
        return autoCommit;
    }

    /** IRDB has no transactions; the flag is remembered so callers see what they set. */
    public void setAutoCommit(boolean autoCommit) {
        this.autoCommit = autoCommit;
    }

    public void commit() {
    }

    public void rollback() {
    }

    public int getTransactionIsolation() {
        return Connection.TRANSACTION_NONE;
    }

    public void setTransactionIsolation(int level) {
    }

    public boolean isReadOnly() {
        return true;
    }

    public void setReadOnly(boolean readOnly) {
    }

    public String getCatalog() {
        return url.database;
    }

    public void setCatalog(String catalog) {
    }

    public String getSchema() {
        return null;
    }

    public String nativeSQL(String sql) {
        return sql;
    }

    public SQLWarning getWarnings() {
        return null;
    }

    public void clearWarnings() {
    }

    @Override
    public String toString() {
        return url.url;
    }
}
