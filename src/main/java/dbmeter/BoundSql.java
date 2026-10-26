package dbmeter;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Time;
import java.sql.Timestamp;
import java.sql.Types;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ThreadLocalRandom;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A SQL statement whose :{name[:type]} placeholders were replaced with JDBC '?' markers. Placeholders name a
 * column of the scenario's CSV data file, or a generator: $rand(lo,hi), $uuid, $thread, $iter, $now.
 */
final class BoundSql {
    private static final Pattern PLACEHOLDER = Pattern.compile(":\\{([^}]*)}");

    final String sql;
    final List<Param> params;

    private BoundSql(String sql, List<Param> params) {
        this.sql = sql;
        this.params = params;
    }

    /** Replaces placeholders outside string literals and comments with '?'. */
    static BoundSql parse(String text) {
        StringBuilder sb = new StringBuilder(text.length());
        List<Param> params = new ArrayList<>();
        Matcher m = PLACEHOLDER.matcher(text);
        int i = 0;
        while (i < text.length()) {
            int end = SqlScript.skipLiteralOrComment(text, i);
            if (end > i) {
                sb.append(text, i, end);
                i = end;
            } else if (text.startsWith(":{", i) && m.find(i) && m.start() == i) {
                params.add(Param.parse(m.group(1)));
                sb.append('?');
                i = m.end();
            } else {
                sb.append(text.charAt(i++));
            }
        }
        return new BoundSql(sb.toString(), params.isEmpty() ? Collections.emptyList() : params);
    }

    void bind(PreparedStatement ps, Row row, ParamContext ctx) throws SQLException {
        for (int i = 0; i < params.size(); i++) {
            params.get(i).bind(ps, i + 1, row, ctx);
        }
    }

    /** Names of data columns referenced by this statement. */
    Set<String> columns() {
        Set<String> names = new LinkedHashSet<>();
        for (Param p : params) {
            if (p.kind == Kind.COLUMN) {
                names.add(p.name);
            }
        }
        return names;
    }

    interface ParamContext {
        int thread();

        long iteration();
    }

    enum Kind { COLUMN, RAND, UUID, THREAD, ITER, NOW }

    static final class Param {
        private static final Set<String> TYPES = new LinkedHashSet<>(List.of(
                "string", "int", "long", "double", "decimal", "bool", "date", "time", "timestamp", "object"));
        private static final Pattern GENERATOR = Pattern.compile("\\$(\\w+)(?:\\(([^)]*)\\))?(?::(\\w+))?");

        final Kind kind;
        final String name;
        final String type;
        private final long lo;
        private final long hi;

        private Param(Kind kind, String name, String type, long lo, long hi) {
            this.kind = kind;
            this.name = name;
            this.type = type;
            this.lo = lo;
            this.hi = hi;
        }

        static Param parse(String spec) {
            spec = spec.trim();
            if (spec.startsWith("$")) {
                Matcher m = GENERATOR.matcher(spec);
                if (!m.matches()) {
                    throw new ConfigException("invalid placeholder :{" + spec + "}");
                }
                String type = m.group(3);
                switch (m.group(1).toLowerCase(Locale.ROOT)) {
                    case "rand": {
                        String[] range = m.group(2) == null ? new String[0] : m.group(2).split(",");
                        if (range.length != 2) {
                            throw new ConfigException("invalid placeholder :{" + spec + "} - use $rand(lo,hi)");
                        }
                        long lo = Long.parseLong(range[0].trim());
                        long hi = Long.parseLong(range[1].trim());
                        if (hi < lo) {
                            throw new ConfigException("invalid placeholder :{" + spec + "} - hi < lo");
                        }
                        return new Param(Kind.RAND, spec, type(type, "long", spec), lo, hi);
                    }
                    case "uuid":
                        return new Param(Kind.UUID, spec, type(type, "string", spec), 0, 0);
                    case "thread":
                        return new Param(Kind.THREAD, spec, type(type, "int", spec), 0, 0);
                    case "iter":
                        return new Param(Kind.ITER, spec, type(type, "long", spec), 0, 0);
                    case "now":
                        return new Param(Kind.NOW, spec, type(type, "timestamp", spec), 0, 0);
                    default:
                        throw new ConfigException("unknown generator in :{" + spec + "} (use $rand, $uuid, $thread, $iter, $now)");
                }
            }
            int colon = spec.lastIndexOf(':');
            String name = (colon < 0 ? spec : spec.substring(0, colon)).trim();
            if (name.isEmpty()) {
                throw new ConfigException("invalid placeholder :{" + spec + "}");
            }
            return new Param(Kind.COLUMN, name, type(colon < 0 ? null : spec.substring(colon + 1), "string", spec), 0, 0);
        }

        private static String type(String t, String def, String spec) {
            if (t == null || t.trim().isEmpty()) {
                return def;
            }
            String type = t.trim().toLowerCase(Locale.ROOT);
            if (!TYPES.contains(type)) {
                throw new ConfigException("unknown type in :{" + spec + "} - use one of " + TYPES);
            }
            return type;
        }

        void bind(PreparedStatement ps, int idx, Row row, ParamContext ctx) throws SQLException {
            String v;
            switch (kind) {
                case COLUMN:
                    if (row == null) {
                        throw new IllegalStateException("placeholder :{" + name + "} needs a params file");
                    }
                    v = row.get(name);
                    break;
                case RAND:
                    v = Long.toString(ThreadLocalRandom.current().nextLong(lo, hi + 1));
                    break;
                case UUID:
                    v = java.util.UUID.randomUUID().toString();
                    break;
                case THREAD:
                    v = Integer.toString(ctx.thread());
                    break;
                case ITER:
                    v = Long.toString(ctx.iteration());
                    break;
                default:
                    v = new Timestamp(System.currentTimeMillis()).toString();
            }
            set(ps, idx, v, type);
        }

        private static void set(PreparedStatement ps, int idx, String v, String type) throws SQLException {
            if (v == null) {
                ps.setNull(idx, sqlType(type));
                return;
            }
            switch (type) {
                case "int":
                    ps.setInt(idx, Integer.parseInt(v.trim()));
                    break;
                case "long":
                    ps.setLong(idx, Long.parseLong(v.trim()));
                    break;
                case "double":
                    ps.setDouble(idx, Double.parseDouble(v.trim()));
                    break;
                case "decimal":
                    ps.setBigDecimal(idx, new BigDecimal(v.trim()));
                    break;
                case "bool":
                    ps.setBoolean(idx, v.trim().equalsIgnoreCase("true") || v.trim().equals("1"));
                    break;
                case "date":
                    ps.setDate(idx, Date.valueOf(v.trim()));
                    break;
                case "time":
                    ps.setTime(idx, Time.valueOf(v.trim()));
                    break;
                case "timestamp":
                    ps.setTimestamp(idx, Timestamp.valueOf(v.trim()));
                    break;
                case "object":
                    ps.setObject(idx, v);
                    break;
                default:
                    ps.setString(idx, v);
            }
        }

        private static int sqlType(String type) {
            switch (type) {
                case "int":
                    return Types.INTEGER;
                case "long":
                    return Types.BIGINT;
                case "double":
                    return Types.DOUBLE;
                case "decimal":
                    return Types.DECIMAL;
                case "bool":
                    return Types.BOOLEAN;
                case "date":
                    return Types.DATE;
                case "time":
                    return Types.TIME;
                case "timestamp":
                    return Types.TIMESTAMP;
                case "object":
                    return Types.OTHER;
                default:
                    return Types.VARCHAR;
            }
        }
    }
}
