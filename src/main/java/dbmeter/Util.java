package dbmeter;

import java.io.IOException;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.SQLException;
import java.util.Locale;

final class Util {
    private Util() {
    }

    static String maskUrl(String url) {
        return url == null ? null : url.replaceAll("(?i)(password|pwd)=([^;&]*)", "$1=****");
    }

    static String hostName() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (Exception e) {
            String h = System.getenv("COMPUTERNAME");
            if (h == null) {
                h = System.getenv("HOSTNAME");
            }
            return h == null ? "unknown" : h;
        }
    }

    /** One-line description of an exception and its causes, with SQL state / vendor code where present. */
    static String describe(Throwable e) {
        StringBuilder sb = new StringBuilder();
        for (Throwable t = e; t != null && sb.length() < 1000; t = t.getCause()) {
            String msg = t.getMessage() == null ? t.getClass().getSimpleName() : t.getMessage().trim();
            if (t instanceof SQLException) {
                SQLException s = (SQLException) t;
                if (s.getSQLState() != null) {
                    msg += " [SQLState " + s.getSQLState() + (s.getErrorCode() != 0 ? ", code " + s.getErrorCode() : "") + "]";
                }
            } else if (!(t instanceof ConfigException) && !(t instanceof Main.UsageException)) {
                msg = t.getClass().getSimpleName() + ": " + msg;
            }
            if (sb.indexOf(msg) < 0) {
                if (sb.length() > 0) {
                    sb.append(" <- ");
                }
                sb.append(msg);
            }
            if (t.getCause() == t) {
                break;
            }
        }
        return sb.toString().replaceAll("\\s+", " ");
    }

    static String ms(long micros) {
        return String.format(Locale.ROOT, "%.2f", micros / 1000.0);
    }

    static String truncate(String s, int max) {
        return s.length() <= max ? s : s.substring(0, max - 3) + "...";
    }

    static String safeFileName(String s) {
        return s.replaceAll("[^A-Za-z0-9._-]", "_");
    }

    static String readFile(Path p) throws IOException {
        String s = new String(Files.readAllBytes(p), StandardCharsets.UTF_8);
        return s.startsWith("﻿") ? s.substring(1) : s;
    }

    static void writeFile(Path p, String content) throws IOException {
        Files.write(p, content.getBytes(StandardCharsets.UTF_8));
    }

    static void closeQuietly(AutoCloseable c) {
        if (c != null) {
            try {
                c.close();
            } catch (Exception ignored) {
                // best effort
            }
        }
    }
}
