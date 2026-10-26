package dbmeter.irdb;

import java.sql.SQLException;
import java.util.Properties;

/** Parsed jdbc:irdb://host[:port]/database[?encrypt=true&timeout=SECONDS] plus user/password properties. */
final class IrdbUrl {
    final String url;
    String host = "localhost";
    int port = 5060;
    String database;
    String user = "default";
    String password = "!TopSecret";
    boolean encrypt;
    int timeoutSeconds;

    private IrdbUrl(String url) {
        this.url = url;
    }

    static IrdbUrl parse(String url, Properties info) throws SQLException {
        IrdbUrl u = new IrdbUrl(url);
        String rest = url.substring(IrdbDriver.PREFIX.length());
        if (!rest.startsWith("//")) {
            throw new SQLException("expected jdbc:irdb://host[:port]/database, got " + url);
        }
        rest = rest.substring(2);
        String query = null;
        int q = rest.indexOf('?');
        if (q >= 0) {
            query = rest.substring(q + 1);
            rest = rest.substring(0, q);
        }
        int slash = rest.indexOf('/');
        if (slash < 0 || slash == rest.length() - 1) {
            throw new SQLException("missing database name in " + url);
        }
        u.database = rest.substring(slash + 1);
        String hostPort = rest.substring(0, slash);
        int colon = hostPort.lastIndexOf(':');
        if (colon >= 0) {
            u.port = Integer.parseInt(hostPort.substring(colon + 1));
            hostPort = hostPort.substring(0, colon);
        }
        if (!hostPort.isEmpty()) {
            u.host = hostPort;
        }
        Properties all = new Properties();
        if (query != null) {
            for (String kv : query.split("&")) {
                int eq = kv.indexOf('=');
                if (eq > 0) {
                    all.setProperty(kv.substring(0, eq), kv.substring(eq + 1));
                }
            }
        }
        if (info != null) {
            all.putAll(info);
        }
        u.user = all.getProperty("user", u.user);
        u.password = all.getProperty("password", u.password);
        u.encrypt = Boolean.parseBoolean(all.getProperty("encrypt", "false"));
        u.timeoutSeconds = Integer.parseInt(all.getProperty("timeout", "0"));
        return u;
    }
}
