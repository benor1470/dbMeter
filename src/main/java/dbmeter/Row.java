package dbmeter;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;

/** One row of parameter values, looked up by column name. */
final class Row {
    private final Map<String, Integer> index;
    private final String[] values;

    Row(Map<String, Integer> index, String[] values) {
        this.index = index;
        this.values = values;
    }

    static Map<String, Integer> index(List<String> columns) {
        Map<String, Integer> index = new HashMap<>();
        for (int i = 0; i < columns.size(); i++) {
            index.put(columns.get(i).trim(), i);
        }
        return index;
    }

    static Row fromProperties(Properties p) {
        Map<String, Integer> index = new HashMap<>();
        String[] values = new String[p.size()];
        int i = 0;
        for (String k : p.stringPropertyNames()) {
            index.put(k, i);
            String v = p.getProperty(k);
            values[i++] = v.equals("NULL") ? null : v;
        }
        return new Row(index, values);
    }

    boolean has(String name) {
        return index.containsKey(name);
    }

    String get(String name) {
        Integer i = index.get(name);
        if (i == null) {
            throw new IllegalArgumentException("no parameter value named '" + name + "'");
        }
        return i < values.length ? values[i] : null;
    }
}
