package dbmeter.irdb;

import java.sql.Connection;
import java.sql.Driver;
import java.sql.DriverPropertyInfo;
import java.sql.SQLException;
import java.sql.SQLFeatureNotSupportedException;
import java.util.Properties;
import java.util.logging.Logger;

/**
 * Minimal JDBC driver for InMemory.Net IRDB on top of the vendor's jirdb client (irdb.IPClient).
 *
 * <pre>jdbc:irdb://host[:port]/database[?encrypt=true&amp;timeout=SECONDS]</pre>
 * Properties: user (default "default"), password (default "!TopSecret"). Default port 5060.
 * IRDB is a read-only analytical store over SQL: queries and server-side prepared statements ('?'
 * parameters) are supported; transactions are accepted as no-ops. Data is loaded with {@link CopyToIrdb}.
 */
public final class IrdbDriver implements Driver {
    static final String PREFIX = "jdbc:irdb:";
    static final String VERSION = "0.1";

    static {
        try {
            java.sql.DriverManager.registerDriver(new IrdbDriver());
        } catch (SQLException e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    @Override
    public Connection connect(String url, Properties info) throws SQLException {
        if (!acceptsURL(url)) {
            return null;
        }
        IrdbUrl u = IrdbUrl.parse(url, info);
        return Jdbc.proxy(Connection.class, new IrdbConnection(u));
    }

    @Override
    public boolean acceptsURL(String url) {
        return url != null && url.startsWith(PREFIX);
    }

    @Override
    public DriverPropertyInfo[] getPropertyInfo(String url, Properties info) {
        return new DriverPropertyInfo[0];
    }

    @Override
    public int getMajorVersion() {
        return 0;
    }

    @Override
    public int getMinorVersion() {
        return 1;
    }

    @Override
    public boolean jdbcCompliant() {
        return false;
    }

    @Override
    public Logger getParentLogger() throws SQLFeatureNotSupportedException {
        throw new SQLFeatureNotSupportedException();
    }
}
