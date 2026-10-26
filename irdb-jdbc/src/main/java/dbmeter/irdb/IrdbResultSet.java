package dbmeter.irdb;

import irdb.InMemoryColumnBase;
import irdb.InMemoryTable;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.SQLWarning;
import java.sql.Statement;
import java.sql.Timestamp;
import java.sql.Types;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.Locale;

/** Backs java.sql.ResultSet over a fully materialized IRDB InMemoryTable (forward-only, read-only). */
public final class IrdbResultSet {
    private final InMemoryTable table;
    private final IrdbStatement statement;
    private int row = -1;
    private boolean wasNull;
    private boolean closed;

    IrdbResultSet(InMemoryTable table, IrdbStatement statement) {
        this.table = table;
        this.statement = statement;
    }

    public boolean next() {
        if (row < table.rowCount) {
            row++;
        }
        return row < table.rowCount;
    }

    public void close() {
        closed = true;
    }

    public boolean isClosed() {
        return closed;
    }

    public boolean wasNull() {
        return wasNull;
    }

    public int findColumn(String label) throws SQLException {
        for (int i = 0; i < table.columnNames.length; i++) {
            if (table.columnNames[i].equalsIgnoreCase(label)) {
                return i + 1;
            }
        }
        throw new SQLException("no column '" + label + "'");
    }

    public Object getObject(int col) throws SQLException {
        if (row < 0 || row >= table.rowCount) {
            throw new SQLException("no current row");
        }
        if (col < 1 || col > table.columns.length) {
            throw new SQLException("column index out of range: " + col);
        }
        InMemoryColumnBase c = table.columns[col - 1];
        Object v = c.isRowNull(row) ? null : c.getValue(row);
        wasNull = v == null;
        return v;
    }

    public Object getObject(String label) throws SQLException {
        return getObject(findColumn(label));
    }

    public String getString(int col) throws SQLException {
        Object v = getObject(col);
        return v == null ? null : v.toString();
    }

    public long getLong(int col) throws SQLException {
        Object v = getObject(col);
        return v == null ? 0 : v instanceof Number ? ((Number) v).longValue() : Long.parseLong(v.toString().trim());
    }

    public int getInt(int col) throws SQLException {
        return (int) getLong(col);
    }

    public double getDouble(int col) throws SQLException {
        Object v = getObject(col);
        return v == null ? 0 : v instanceof Number ? ((Number) v).doubleValue() : Double.parseDouble(v.toString().trim());
    }

    public BigDecimal getBigDecimal(int col) throws SQLException {
        Object v = getObject(col);
        if (v == null) {
            return null;
        }
        if (v instanceof BigDecimal) {
            return (BigDecimal) v;
        }
        if (v instanceof Double || v instanceof Float) {
            return BigDecimal.valueOf(((Number) v).doubleValue());
        }
        return new BigDecimal(v.toString().trim());
    }

    public boolean getBoolean(int col) throws SQLException {
        Object v = getObject(col);
        return v != null && (v instanceof Boolean ? (Boolean) v : "true".equalsIgnoreCase(v.toString()) || "1".equals(v.toString()));
    }

    public Timestamp getTimestamp(int col) throws SQLException {
        Object v = getObject(col);
        if (v == null || v instanceof Timestamp) {
            return (Timestamp) v;
        }
        if (v instanceof java.util.Date) {
            return new Timestamp(((java.util.Date) v).getTime());
        }
        if (v instanceof LocalDateTime) {
            return Timestamp.valueOf((LocalDateTime) v);
        }
        return Timestamp.valueOf(v.toString());
    }

    public Date getDate(int col) throws SQLException {
        Object v = getObject(col);
        if (v == null) {
            return null;
        }
        if (v instanceof LocalDate) {
            return Date.valueOf((LocalDate) v);
        }
        return new Date(getTimestamp(col).getTime());
    }

    public String getString(String label) throws SQLException {
        return getString(findColumn(label));
    }

    public long getLong(String label) throws SQLException {
        return getLong(findColumn(label));
    }

    public int getInt(String label) throws SQLException {
        return getInt(findColumn(label));
    }

    public double getDouble(String label) throws SQLException {
        return getDouble(findColumn(label));
    }

    public BigDecimal getBigDecimal(String label) throws SQLException {
        return getBigDecimal(findColumn(label));
    }

    public ResultSetMetaData getMetaData() {
        return Jdbc.proxy(ResultSetMetaData.class, new Meta());
    }

    public Statement getStatement() {
        return null;
    }

    public int getType() {
        return ResultSet.TYPE_FORWARD_ONLY;
    }

    public int getConcurrency() {
        return ResultSet.CONCUR_READ_ONLY;
    }

    public void setFetchSize(int rows) {
    }

    public int getFetchSize() {
        return table.rowCount;
    }

    public SQLWarning getWarnings() {
        return null;
    }

    public void clearWarnings() {
    }

    /** Backs ResultSetMetaData; IRDB reports .NET type names such as System.Int64. */
    public final class Meta {
        public int getColumnCount() {
            return table.columnNames.length;
        }

        public String getColumnLabel(int col) {
            return table.columnNames[col - 1];
        }

        public String getColumnName(int col) {
            return table.columnNames[col - 1];
        }

        public String getColumnTypeName(int col) {
            return table.datatypes[col - 1];
        }

        public int getColumnType(int col) {
            String t = table.datatypes[col - 1].toLowerCase(Locale.ROOT).replace("system.", "");
            switch (t) {
                case "int64":
                    return Types.BIGINT;
                case "int32":
                    return Types.INTEGER;
                case "int16":
                    return Types.SMALLINT;
                case "byte":
                    return Types.TINYINT;
                case "boolean":
                    return Types.BOOLEAN;
                case "double":
                    return Types.DOUBLE;
                case "single":
                    return Types.REAL;
                case "decimal":
                    return Types.DECIMAL;
                case "datetime":
                    return Types.TIMESTAMP;
                case "string":
                    return Types.VARCHAR;
                default:
                    return Types.OTHER;
            }
        }

        public String getColumnClassName(int col) {
            return Object.class.getName();
        }

        public int isNullable(int col) {
            return ResultSetMetaData.columnNullableUnknown;
        }

        public String getTableName(int col) {
            return "";
        }

        public String getSchemaName(int col) {
            return "";
        }

        public String getCatalogName(int col) {
            return "";
        }
    }
}
