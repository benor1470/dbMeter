package dbmeter.irdb;

import irdb.InMemoryStatement;
import irdb.InMemoryTable;

import java.io.IOException;
import java.math.BigDecimal;
import java.sql.Connection;
import java.sql.Date;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.SQLWarning;
import java.sql.Time;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.Arrays;

/**
 * Backs java.sql.Statement and PreparedStatement. A prepared statement is prepared on the server on first
 * execution ('?' markers) and executed with its parameters as strings, as the jirdb client requires.
 */
public final class IrdbStatement {
    private final IrdbConnection con;
    private final String preparedSql;
    private final boolean hasMarkers;
    private String serverName;
    private String[] params = new String[0];
    private ResultSet current;
    private boolean closed;

    IrdbStatement(IrdbConnection con, String preparedSql) {
        this.con = con;
        this.preparedSql = preparedSql == null ? null : stripComments(preparedSql);
        this.hasMarkers = this.preparedSql != null && this.preparedSql.indexOf('?') >= 0 && hasParameterMarkers(this.preparedSql);
    }

    /** IRDB's parser rejects comments, so they are removed (outside literals) before sending. */
    static String stripComments(String sql) {
        StringBuilder sb = new StringBuilder(sql.length());
        int n = sql.length();
        int i = 0;
        while (i < n) {
            char ch = sql.charAt(i);
            if (ch == '\'' || ch == '"' || ch == '[') {
                int end = sql.indexOf(ch == '[' ? ']' : ch, i + 1);
                end = end < 0 ? n : end + 1;
                sb.append(sql, i, end);
                i = end;
            } else if (ch == '-' && i + 1 < n && sql.charAt(i + 1) == '-') {
                int end = sql.indexOf('\n', i);
                i = end < 0 ? n : end;
            } else if (ch == '/' && i + 1 < n && sql.charAt(i + 1) == '*') {
                int end = sql.indexOf("*/", i + 2);
                i = end < 0 ? n : end + 2;
                sb.append(' ');
            } else {
                sb.append(ch);
                i++;
            }
        }
        return sb.toString().trim();
    }

    /** True if comment-free sql has a '?' outside string literals and quoted identifiers. */
    static boolean hasParameterMarkers(String sql) {
        for (int i = 0; i < sql.length(); i++) {
            char ch = sql.charAt(i);
            if (ch == '\'' || ch == '"' || ch == '[') {
                i = sql.indexOf(ch == '[' ? ']' : ch, i + 1);
                if (i < 0) {
                    return false;
                }
            } else if (ch == '?') {
                return true;
            }
        }
        return false;
    }

    // ---- execution ----

    public boolean execute(String sql) throws SQLException {
        run(sql);
        return true;
    }

    public ResultSet executeQuery(String sql) throws SQLException {
        return run(sql);
    }

    public boolean execute() throws SQLException {
        executeQuery();
        return true;
    }

    public ResultSet executeQuery() throws SQLException {
        check();
        if (!hasMarkers) {
            // IRDB fails to prepare statements without parameters, so those run as plain queries.
            return run(preparedSql);
        }
        try {
            if (serverName == null) {
                InMemoryStatement s = con.client.prepareStatement(con.url.database, preparedSql);
                if (s == null) {
                    throw con.error("IRDB prepare failed", "42000");
                }
                serverName = s.statementName;
                if (s.noVariables > params.length) {
                    params = Arrays.copyOf(params, s.noVariables);
                }
            }
            InMemoryTable t = con.client.statementExecute(con.url.database, serverName, new ArrayList<>(Arrays.asList(params)));
            return result(t);
        } catch (IOException e) {
            throw new SQLException("IRDB I/O error: " + e.getMessage(), "08006", e);
        }
    }

    private ResultSet run(String sql) throws SQLException {
        check();
        try {
            return result(con.client.execute(con.url.database, stripComments(sql)));
        } catch (IOException e) {
            throw new SQLException("IRDB I/O error: " + e.getMessage(), "08006", e);
        }
    }

    private ResultSet result(InMemoryTable t) throws SQLException {
        if (t == null) {
            throw con.error("IRDB query failed", "42000");
        }
        closeResult();
        current = Jdbc.proxy(ResultSet.class, new IrdbResultSet(t, this));
        return current;
    }

    public ResultSet getResultSet() {
        return current;
    }

    public int getUpdateCount() {
        return -1;
    }

    public boolean getMoreResults() throws SQLException {
        closeResult();
        return false;
    }

    private void closeResult() throws SQLException {
        if (current != null) {
            current.close();
            current = null;
        }
    }

    private void check() throws SQLException {
        con.checkOpen();
        if (closed) {
            throw new SQLException("statement is closed");
        }
    }

    public void close() throws SQLException {
        if (closed) {
            return;
        }
        closed = true;
        closeResult();
        if (serverName != null && !con.isClosed()) {
            try {
                con.client.dropStatement(con.url.database, serverName);
            } catch (IOException ignored) {
                // the statement dies with the session anyway
            }
        }
    }

    public boolean isClosed() {
        return closed;
    }

    // ---- parameters (sent to IRDB as strings) ----

    private void set(int index, Object value) throws SQLException {
        if (preparedSql == null) {
            throw new SQLException("not a prepared statement");
        }
        if (index < 1) {
            throw new SQLException("parameter index starts at 1: " + index);
        }
        if (index > params.length) {
            params = Arrays.copyOf(params, index);
        }
        params[index - 1] = value == null ? null : value.toString();
    }

    public void setNull(int i, int sqlType) throws SQLException {
        set(i, null);
    }

    public void setString(int i, String v) throws SQLException {
        set(i, v);
    }

    public void setInt(int i, int v) throws SQLException {
        set(i, v);
    }

    public void setLong(int i, long v) throws SQLException {
        set(i, v);
    }

    public void setShort(int i, short v) throws SQLException {
        set(i, v);
    }

    public void setDouble(int i, double v) throws SQLException {
        set(i, v);
    }

    public void setFloat(int i, float v) throws SQLException {
        set(i, v);
    }

    public void setBigDecimal(int i, BigDecimal v) throws SQLException {
        set(i, v == null ? null : v.toPlainString());
    }

    public void setBoolean(int i, boolean v) throws SQLException {
        set(i, v);
    }

    public void setDate(int i, Date v) throws SQLException {
        set(i, v);
    }

    public void setTime(int i, Time v) throws SQLException {
        set(i, v);
    }

    public void setTimestamp(int i, Timestamp v) throws SQLException {
        set(i, v);
    }

    public void setObject(int i, Object v) throws SQLException {
        set(i, v);
    }

    public void clearParameters() {
        Arrays.fill(params, null);
    }

    // ---- accepted no-ops / trivial getters ----

    public void setFetchSize(int rows) {
    }

    public int getFetchSize() {
        return 0;
    }

    public void setQueryTimeout(int seconds) {
    }

    public int getQueryTimeout() {
        return 0;
    }

    public void setMaxRows(int max) {
    }

    public void setEscapeProcessing(boolean enable) {
    }

    public Connection getConnection() throws SQLException {
        throw new SQLException("getConnection is not supported");
    }

    public SQLWarning getWarnings() {
        return null;
    }

    public void clearWarnings() {
    }
}
