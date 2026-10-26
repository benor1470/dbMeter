package dbmeter;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Small JSON writer/parser for report files (objects, arrays, strings, numbers, booleans, null). */
final class Json {
    private Json() {
    }

    static String write(Object v) {
        StringBuilder sb = new StringBuilder();
        write(sb, v, 0);
        return sb.append('\n').toString();
    }

    private static void write(StringBuilder sb, Object v, int indent) {
        if (v == null) {
            sb.append("null");
        } else if (v instanceof String) {
            quote(sb, (String) v);
        } else if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            sb.append(Double.isNaN(d) || Double.isInfinite(d) ? "null" : Double.toString(d));
        } else if (v instanceof Number || v instanceof Boolean) {
            sb.append(v);
        } else if (v instanceof Map) {
            Map<?, ?> m = (Map<?, ?>) v;
            if (m.isEmpty()) {
                sb.append("{}");
                return;
            }
            sb.append("{\n");
            boolean first = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                if (!first) {
                    sb.append(",\n");
                }
                first = false;
                pad(sb, indent + 1);
                quote(sb, String.valueOf(e.getKey()));
                sb.append(": ");
                write(sb, e.getValue(), indent + 1);
            }
            sb.append('\n');
            pad(sb, indent);
            sb.append('}');
        } else if (v instanceof Collection) {
            Collection<?> c = (Collection<?>) v;
            boolean inline = c.stream().allMatch(x -> x == null || x instanceof Number || x instanceof Boolean);
            sb.append('[');
            boolean first = true;
            for (Object x : c) {
                if (!first) {
                    sb.append(inline ? ", " : ",");
                }
                first = false;
                if (!inline) {
                    sb.append('\n');
                    pad(sb, indent + 1);
                }
                write(sb, x, indent + 1);
            }
            if (!inline && !c.isEmpty()) {
                sb.append('\n');
                pad(sb, indent);
            }
            sb.append(']');
        } else {
            quote(sb, String.valueOf(v));
        }
    }

    private static void pad(StringBuilder sb, int indent) {
        for (int i = 0; i < indent; i++) {
            sb.append("  ");
        }
    }

    private static void quote(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            switch (ch) {
                case '"':
                    sb.append("\\\"");
                    break;
                case '\\':
                    sb.append("\\\\");
                    break;
                case '\n':
                    sb.append("\\n");
                    break;
                case '\r':
                    sb.append("\\r");
                    break;
                case '\t':
                    sb.append("\\t");
                    break;
                default:
                    if (ch < 0x20) {
                        sb.append(String.format("\\u%04x", (int) ch));
                    } else {
                        sb.append(ch);
                    }
            }
        }
        sb.append('"');
    }

    static Object parse(String text) {
        Parser p = new Parser(text);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.pos != text.length()) {
            throw p.error("trailing content");
        }
        return v;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> obj(Object o) {
        return o == null ? Collections.emptyMap() : (Map<String, Object>) o;
    }

    @SuppressWarnings("unchecked")
    static List<Object> list(Object o) {
        return o == null ? Collections.emptyList() : (List<Object>) o;
    }

    static long num(Object o) {
        return o == null ? 0 : ((Number) o).longValue();
    }

    private static final class Parser {
        private final String s;
        int pos;

        Parser(String s) {
            this.s = s;
        }

        Object value() {
            if (pos >= s.length()) {
                throw error("unexpected end");
            }
            char ch = s.charAt(pos);
            switch (ch) {
                case '{':
                    return object();
                case '[':
                    return array();
                case '"':
                    return string();
                case 't':
                    return literal("true", Boolean.TRUE);
                case 'f':
                    return literal("false", Boolean.FALSE);
                case 'n':
                    return literal("null", null);
                default:
                    return number();
            }
        }

        private Map<String, Object> object() {
            Map<String, Object> m = new LinkedHashMap<>();
            pos++;
            ws();
            if (peek() == '}') {
                pos++;
                return m;
            }
            while (true) {
                ws();
                if (peek() != '"') {
                    throw error("expected key");
                }
                String k = string();
                ws();
                expect(':');
                ws();
                m.put(k, value());
                ws();
                if (peek() == ',') {
                    pos++;
                } else {
                    expect('}');
                    return m;
                }
            }
        }

        private List<Object> array() {
            List<Object> l = new ArrayList<>();
            pos++;
            ws();
            if (peek() == ']') {
                pos++;
                return l;
            }
            while (true) {
                ws();
                l.add(value());
                ws();
                if (peek() == ',') {
                    pos++;
                } else {
                    expect(']');
                    return l;
                }
            }
        }

        private String string() {
            StringBuilder sb = new StringBuilder();
            pos++;
            while (pos < s.length()) {
                char ch = s.charAt(pos++);
                if (ch == '"') {
                    return sb.toString();
                }
                if (ch != '\\') {
                    sb.append(ch);
                    continue;
                }
                char e = s.charAt(pos++);
                switch (e) {
                    case 'n':
                        sb.append('\n');
                        break;
                    case 'r':
                        sb.append('\r');
                        break;
                    case 't':
                        sb.append('\t');
                        break;
                    case 'b':
                        sb.append('\b');
                        break;
                    case 'f':
                        sb.append('\f');
                        break;
                    case 'u':
                        sb.append((char) Integer.parseInt(s.substring(pos, pos + 4), 16));
                        pos += 4;
                        break;
                    default:
                        sb.append(e);
                }
            }
            throw error("unterminated string");
        }

        private Object number() {
            int start = pos;
            while (pos < s.length() && "+-0123456789.eE".indexOf(s.charAt(pos)) >= 0) {
                pos++;
            }
            String n = s.substring(start, pos);
            if (n.isEmpty()) {
                throw error("unexpected character '" + s.charAt(start) + "'");
            }
            if (n.indexOf('.') < 0 && n.indexOf('e') < 0 && n.indexOf('E') < 0) {
                return Long.parseLong(n);
            }
            return Double.parseDouble(n);
        }

        private Object literal(String word, Object value) {
            if (!s.startsWith(word, pos)) {
                throw error("invalid literal");
            }
            pos += word.length();
            return value;
        }

        void ws() {
            while (pos < s.length() && Character.isWhitespace(s.charAt(pos))) {
                pos++;
            }
        }

        private char peek() {
            return pos < s.length() ? s.charAt(pos) : '\0';
        }

        private void expect(char ch) {
            if (peek() != ch) {
                throw error("expected '" + ch + "'");
            }
            pos++;
        }

        IllegalArgumentException error(String msg) {
            return new IllegalArgumentException("invalid JSON at offset " + pos + ": " + msg);
        }
    }
}
