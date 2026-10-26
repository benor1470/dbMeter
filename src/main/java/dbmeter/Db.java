package dbmeter;

import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.Driver;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.ServiceConfigurationError;
import java.util.ServiceLoader;
import java.util.stream.Stream;

/** Loads the JDBC driver from the configured jar(s) and opens connections with the configured properties. */
final class Db {
    final Driver driver;
    final ClassLoader loader;
    final String url;
    private final Properties props;
    private final List<String> initSql;
    private final Integer isolation;

    private Db(Driver driver, ClassLoader loader, String url, Properties props, List<String> initSql, Integer isolation) {
        this.driver = driver;
        this.loader = loader;
        this.url = url;
        this.props = props;
        this.initSql = initSql;
        this.isolation = isolation;
    }

    static Db fromConfig(Config c) throws Exception {
        String url = c.require("jdbc.url");
        Properties props = c.withPrefix("jdbc.prop.");
        String user = c.get("jdbc.user");
        if (user != null) {
            props.setProperty("user", user);
        }
        String password = c.get("jdbc.password");
        if (password != null) {
            props.setProperty("password", password);
        }
        ClassLoader loader = driverClassLoader(c);
        Driver driver = loadDriver(c, loader, url);
        List<String> init = SqlScript.loadOptional(c, "connection.init");
        String iso = c.get("connection.isolation");
        return new Db(driver, loader, url, props, init == null ? Collections.emptyList() : init,
                iso == null || iso.isEmpty() ? null : isolationLevel(iso));
    }

    Connection connect() throws SQLException {
        Thread current = Thread.currentThread();
        ClassLoader prev = current.getContextClassLoader();
        current.setContextClassLoader(loader);
        Connection con;
        try {
            con = driver.connect(url, props);
        } finally {
            current.setContextClassLoader(prev);
        }
        if (con == null) {
            throw new SQLException("driver " + driver.getClass().getName() + " does not accept URL " + Util.maskUrl(url));
        }
        try {
            if (isolation != null) {
                con.setTransactionIsolation(isolation);
            }
            if (!initSql.isEmpty()) {
                try (Statement st = con.createStatement()) {
                    for (String sql : initSql) {
                        st.execute(sql);
                    }
                }
            }
        } catch (SQLException e) {
            Util.closeQuietly(con);
            throw e;
        }
        return con;
    }

    static Map<String, Object> describe(Connection con) throws SQLException {
        DatabaseMetaData md = con.getMetaData();
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("product", md.getDatabaseProductName());
        m.put("productVersion", md.getDatabaseProductVersion());
        m.put("driver", md.getDriverName());
        m.put("driverVersion", md.getDriverVersion());
        m.put("jdbcVersion", md.getJDBCMajorVersion() + "." + md.getJDBCMinorVersion());
        m.put("url", Util.maskUrl(md.getURL()));
        m.put("user", md.getUserName());
        return m;
    }

    private static ClassLoader driverClassLoader(Config c) throws Exception {
        String jars = c.get("driver.jar");
        List<URL> urls = new ArrayList<>();
        if (jars != null) {
            for (String part : jars.split("[,;]")) {
                if (part.trim().isEmpty()) {
                    continue;
                }
                Path p = c.resolve(part.trim());
                if (Files.isDirectory(p)) {
                    try (Stream<Path> s = Files.list(p)) {
                        for (Iterator<Path> it = s.sorted().iterator(); it.hasNext(); ) {
                            Path f = it.next();
                            if (f.getFileName().toString().toLowerCase(Locale.ROOT).endsWith(".jar")) {
                                urls.add(f.toUri().toURL());
                            }
                        }
                    }
                } else if (Files.isRegularFile(p)) {
                    urls.add(p.toUri().toURL());
                } else {
                    throw new ConfigException("driver.jar: not found: " + p);
                }
            }
        }
        ClassLoader parent = Db.class.getClassLoader();
        return urls.isEmpty() ? parent : new URLClassLoader(urls.toArray(new URL[0]), parent);
    }

    private static Driver loadDriver(Config c, ClassLoader loader, String url) throws Exception {
        String cls = c.get("driver.class");
        if (cls != null && !cls.isEmpty()) {
            try {
                return (Driver) Class.forName(cls, true, loader).getDeclaredConstructor().newInstance();
            } catch (ClassNotFoundException e) {
                throw new ConfigException("driver.class " + cls + " not found in driver.jar");
            }
        }
        Iterator<Driver> it = ServiceLoader.load(Driver.class, loader).iterator();
        while (true) {
            try {
                if (!it.hasNext()) {
                    break;
                }
                Driver d = it.next();
                if (d.acceptsURL(url)) {
                    return d;
                }
            } catch (ServiceConfigurationError | SQLException ignored) {
                // skip drivers that fail to load or reject the URL
            }
        }
        throw new ConfigException("no JDBC driver accepts URL " + Util.maskUrl(url)
                + " - set driver.jar to the vendor's driver jar (and driver.class if it is not auto-registered)");
    }

    static int isolationLevel(String name) {
        switch (name.trim().toUpperCase(Locale.ROOT).replace(' ', '_')) {
            case "NONE":
                return Connection.TRANSACTION_NONE;
            case "READ_UNCOMMITTED":
                return Connection.TRANSACTION_READ_UNCOMMITTED;
            case "READ_COMMITTED":
                return Connection.TRANSACTION_READ_COMMITTED;
            case "REPEATABLE_READ":
                return Connection.TRANSACTION_REPEATABLE_READ;
            case "SERIALIZABLE":
                return Connection.TRANSACTION_SERIALIZABLE;
            default:
                throw new ConfigException("connection.isolation: unknown level '" + name + "'");
        }
    }

    static String isolationName(int level) {
        switch (level) {
            case Connection.TRANSACTION_NONE:
                return "NONE";
            case Connection.TRANSACTION_READ_UNCOMMITTED:
                return "READ_UNCOMMITTED";
            case Connection.TRANSACTION_READ_COMMITTED:
                return "READ_COMMITTED";
            case Connection.TRANSACTION_REPEATABLE_READ:
                return "REPEATABLE_READ";
            case Connection.TRANSACTION_SERIALIZABLE:
                return "SERIALIZABLE";
            default:
                return String.valueOf(level);
        }
    }
}
