package dbmeter;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Duration;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Properties-based configuration. Relative paths resolve against the config file's directory and values may
 * reference ${env:NAME}, ${sys:name} or ${other.property}, each with an optional ":-default".
 */
final class Config {
    private static final Pattern VAR = Pattern.compile("\\$\\{(env:|sys:)?([A-Za-z0-9_.]+)(?::-([^}]*))?}");
    private static final Pattern DURATION = Pattern.compile("(\\d+(?:\\.\\d+)?)\\s*(ms|s|m|h)?");

    private final Properties props = new Properties();
    private final Path baseDir;

    private Config(Path baseDir) {
        this.baseDir = baseDir;
    }

    static Config load(Path file, Map<String, String> overrides) throws IOException {
        Path abs = file.toAbsolutePath().normalize();
        Config c = new Config(abs.getParent());
        try (Reader r = Files.newBufferedReader(abs, StandardCharsets.UTF_8)) {
            c.props.load(r);
        }
        c.props.putAll(overrides);
        return c;
    }

    String get(String key) {
        String v = props.getProperty(key);
        return v == null ? null : expand(v.trim(), 0);
    }

    String get(String key, String def) {
        String v = get(key);
        return v == null || v.isEmpty() ? def : v;
    }

    String require(String key) {
        String v = get(key);
        if (v == null || v.isEmpty()) {
            throw new ConfigException("missing required property '" + key + "'");
        }
        return v;
    }

    int getInt(String key, int def) {
        return (int) getLong(key, def);
    }

    long getLong(String key, long def) {
        String v = get(key);
        if (v == null || v.isEmpty()) {
            return def;
        }
        try {
            return Long.parseLong(v);
        } catch (NumberFormatException e) {
            throw new ConfigException(key + ": not a number: '" + v + "'");
        }
    }

    boolean getBool(String key, boolean def) {
        String v = get(key);
        if (v == null || v.isEmpty()) {
            return def;
        }
        if (v.equalsIgnoreCase("true") || v.equalsIgnoreCase("yes")) {
            return true;
        }
        if (v.equalsIgnoreCase("false") || v.equalsIgnoreCase("no")) {
            return false;
        }
        throw new ConfigException(key + ": expected true/false, got '" + v + "'");
    }

    Duration getDuration(String key, Duration def) {
        String v = get(key);
        return v == null || v.isEmpty() ? def : parseDuration(v, key);
    }

    /** Parses "500ms", "30s", "5m", "1.5h"; a bare number means seconds. */
    static Duration parseDuration(String v, String what) {
        Matcher m = DURATION.matcher(v.trim());
        if (!m.matches()) {
            throw new ConfigException(what + ": invalid duration '" + v + "' (use e.g. 500ms, 30s, 5m, 1h)");
        }
        double n = Double.parseDouble(m.group(1));
        String unit = m.group(2) == null ? "s" : m.group(2);
        double ms;
        switch (unit) {
            case "ms":
                ms = n;
                break;
            case "m":
                ms = n * 60_000;
                break;
            case "h":
                ms = n * 3_600_000;
                break;
            default:
                ms = n * 1000;
        }
        return Duration.ofMillis(Math.round(ms));
    }

    Path resolve(String path) {
        Path p = Paths.get(path);
        return p.isAbsolute() ? p : baseDir.resolve(p).normalize();
    }

    Path getPath(String key) {
        String v = get(key);
        return v == null || v.isEmpty() ? null : resolve(v);
    }

    /** All properties starting with prefix, with the prefix removed and values expanded. */
    Properties withPrefix(String prefix) {
        Properties p = new Properties();
        for (String k : props.stringPropertyNames()) {
            if (k.startsWith(prefix) && k.length() > prefix.length()) {
                p.setProperty(k.substring(prefix.length()), get(k));
            }
        }
        return p;
    }

    /** For keys like "scenario.orders.file", groupNames("scenario.") returns "orders" (sorted). */
    Set<String> groupNames(String prefix) {
        Set<String> names = new TreeSet<>();
        for (String k : props.stringPropertyNames()) {
            if (k.startsWith(prefix)) {
                String rest = k.substring(prefix.length());
                int dot = rest.indexOf('.');
                if (dot > 0) {
                    names.add(rest.substring(0, dot));
                }
            }
        }
        return names;
    }

    private String expand(String v, int depth) {
        if (depth > 10 || v.indexOf("${") < 0) {
            return v;
        }
        Matcher m = VAR.matcher(v);
        StringBuffer sb = new StringBuffer();
        while (m.find()) {
            String kind = m.group(1);
            String name = m.group(2);
            String val;
            if ("env:".equals(kind)) {
                val = System.getenv(name);
            } else if ("sys:".equals(kind)) {
                val = System.getProperty(name);
            } else {
                String raw = props.getProperty(name);
                val = raw == null ? null : expand(raw.trim(), depth + 1);
            }
            if (val == null) {
                val = m.group(3);
            }
            m.appendReplacement(sb, Matcher.quoteReplacement(val == null ? m.group(0) : val));
        }
        m.appendTail(sb);
        return sb.toString();
    }
}
