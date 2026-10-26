package dbmeter;

import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Minimal RFC 4180 CSV reader. Unquoted fields are trimmed, an unquoted NULL becomes null and blank lines are
 * skipped.
 */
final class Csv {
    private Csv() {
    }

    static List<List<String>> read(Path p) throws IOException {
        return parse(Util.readFile(p));
    }

    static List<List<String>> parse(String text) {
        List<List<String>> rows = new ArrayList<>();
        List<String> row = new ArrayList<>();
        StringBuilder field = new StringBuilder();
        boolean inQuotes = false;
        boolean quoted = false;
        boolean rowHasContent = false;
        int n = text.length();
        for (int i = 0; i < n; i++) {
            char ch = text.charAt(i);
            if (inQuotes) {
                if (ch == '"') {
                    if (i + 1 < n && text.charAt(i + 1) == '"') {
                        field.append('"');
                        i++;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    field.append(ch);
                }
            } else if (ch == '"' && field.toString().trim().isEmpty()) {
                field.setLength(0);
                inQuotes = true;
                quoted = true;
                rowHasContent = true;
            } else if (ch == ',') {
                row.add(value(field, quoted));
                field.setLength(0);
                quoted = false;
                rowHasContent = true;
            } else if (ch == '\n' || ch == '\r') {
                if (ch == '\r' && i + 1 < n && text.charAt(i + 1) == '\n') {
                    i++;
                }
                if (rowHasContent || !field.toString().trim().isEmpty()) {
                    row.add(value(field, quoted));
                    rows.add(row);
                }
                row = new ArrayList<>();
                field.setLength(0);
                quoted = false;
                rowHasContent = false;
            } else {
                field.append(ch);
            }
        }
        if (rowHasContent || !field.toString().trim().isEmpty()) {
            row.add(value(field, quoted));
            rows.add(row);
        }
        return rows;
    }

    private static String value(StringBuilder field, boolean quoted) {
        if (quoted) {
            return field.toString();
        }
        String v = field.toString().trim();
        return v.equals("NULL") ? null : v;
    }
}
