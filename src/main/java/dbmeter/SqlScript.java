package dbmeter;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * Splits SQL scripts into statements. With the default ";" delimiter the splitter understands quoted strings,
 * quoted identifiers, line and block comments and PostgreSQL dollar quoting, so function bodies survive intact.
 * Any other delimiter (e.g. "/" for Oracle PL/SQL, "GO" for SQL Server) must stand alone on its own line, and
 * "none" sends the whole text as one statement.
 */
final class SqlScript {
    private SqlScript() {
    }

    /** Statements from {@code <prefix>.file} or {@code <prefix>.sql}; fails if neither is set. */
    static List<String> load(Config c, String prefix) throws IOException {
        List<String> stmts = loadOptional(c, prefix);
        if (stmts == null) {
            throw new ConfigException(prefix + ": set " + prefix + ".file or " + prefix + ".sql");
        }
        if (stmts.isEmpty()) {
            throw new ConfigException(prefix + ": no SQL statements found");
        }
        return stmts;
    }

    static List<String> loadOptional(Config c, String prefix) throws IOException {
        String file = c.get(prefix + ".file");
        String text;
        if (file != null && !file.isEmpty()) {
            text = Util.readFile(c.resolve(file));
        } else {
            text = c.get(prefix + ".sql");
            if (text == null || text.isEmpty()) {
                return null;
            }
        }
        return split(text, c.get(prefix + ".delimiter", c.get("sql.delimiter", ";")));
    }

    static List<String> split(String text, String delimiter) {
        if (delimiter.equalsIgnoreCase("none")) {
            List<String> one = new ArrayList<>();
            if (!text.trim().isEmpty()) {
                one.add(text.trim());
            }
            return one;
        }
        return delimiter.equals(";") ? splitSemicolons(text) : splitLines(text, delimiter);
    }

    private static List<String> splitLines(String text, String delimiter) {
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        for (String line : text.split("\r?\n", -1)) {
            if (line.trim().equalsIgnoreCase(delimiter)) {
                add(out, cur.toString());
                cur.setLength(0);
            } else {
                cur.append(line).append('\n');
            }
        }
        add(out, cur.toString());
        return out;
    }

    private static void add(List<String> out, String stmt) {
        String s = stmt.trim();
        if (!s.isEmpty() && hasCode(s)) {
            out.add(s);
        }
    }

    private static List<String> splitSemicolons(String text) {
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        int n = text.length();
        int i = 0;
        while (i < n) {
            int end = skipLiteralOrComment(text, i);
            if (end == i) {
                if (text.charAt(i) == ';') {
                    add(out, cur.toString());
                    cur.setLength(0);
                    i++;
                    continue;
                }
                end = i + 1;
            }
            cur.append(text, i, end);
            i = end;
        }
        add(out, cur.toString());
        return out;
    }

    /**
     * If a quoted string/identifier, comment or dollar-quoted block starts at i, returns the index just after it;
     * otherwise returns i.
     */
    static int skipLiteralOrComment(String text, int i) {
        int n = text.length();
        char ch = text.charAt(i);
        if (ch == '\'' || ch == '"' || ch == '`') {
            return skipQuoted(text, i, ch);
        }
        if (ch == '-' && i + 1 < n && text.charAt(i + 1) == '-') {
            int end = text.indexOf('\n', i);
            return end < 0 ? n : end;
        }
        if (ch == '/' && i + 1 < n && text.charAt(i + 1) == '*') {
            int end = text.indexOf("*/", i + 2);
            return end < 0 ? n : end + 2;
        }
        if (ch == '$') {
            return skipDollarQuoted(text, i);
        }
        return i;
    }

    private static int skipQuoted(String text, int start, char quote) {
        int i = start + 1;
        while (i < text.length()) {
            if (text.charAt(i) == quote) {
                if (i + 1 < text.length() && text.charAt(i + 1) == quote) {
                    i += 2;
                    continue;
                }
                return i + 1;
            }
            i++;
        }
        return text.length();
    }

    /** Returns the index after a $tag$...$tag$ block starting at start, or start if there is none. */
    private static int skipDollarQuoted(String text, int start) {
        int i = start + 1;
        if (i < text.length() && (Character.isLetter(text.charAt(i)) || text.charAt(i) == '_')) {
            while (i < text.length() && (Character.isLetterOrDigit(text.charAt(i)) || text.charAt(i) == '_')) {
                i++;
            }
        }
        if (i >= text.length() || text.charAt(i) != '$') {
            return start;
        }
        String tag = text.substring(start, i + 1);
        int close = text.indexOf(tag, i + 1);
        return close < 0 ? text.length() : close + tag.length();
    }

    /** True if the statement contains something besides comments. */
    private static boolean hasCode(String s) {
        String stripped = s.replaceAll("(?s)/\\*.*?\\*/", " ").replaceAll("--[^\n]*", " ");
        return !stripped.trim().isEmpty();
    }
}
