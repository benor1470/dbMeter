package dbmeter;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicLong;

/** Rows of a CSV data file, handed out sequentially (shared cursor across threads, wrapping) or at random. */
final class ParamSource {
    final Path file;
    final List<String> columns;
    private final Map<String, Integer> index;
    private final List<String[]> rows;
    private final boolean random;
    private final AtomicLong cursor = new AtomicLong();

    private ParamSource(Path file, List<String> columns, List<String[]> rows, boolean random) {
        this.file = file;
        this.columns = columns;
        this.index = Row.index(columns);
        this.rows = rows;
        this.random = random;
    }

    static ParamSource load(Path file, String mode) throws IOException {
        if (!Files.isRegularFile(file)) {
            throw new ConfigException("params file not found: " + file);
        }
        boolean random;
        if (mode.equalsIgnoreCase("random")) {
            random = true;
        } else if (mode.equalsIgnoreCase("sequential")) {
            random = false;
        } else {
            throw new ConfigException("params mode must be 'sequential' or 'random', got '" + mode + "'");
        }
        List<List<String>> data = Csv.read(file);
        if (data.size() < 2) {
            throw new ConfigException(file + ": needs a header row and at least one data row");
        }
        List<String[]> rows = new ArrayList<>(data.size() - 1);
        for (List<String> r : data.subList(1, data.size())) {
            rows.add(r.toArray(new String[0]));
        }
        return new ParamSource(file, data.get(0), rows, random);
    }

    Row next() {
        int i = random
                ? ThreadLocalRandom.current().nextInt(rows.size())
                : (int) Math.floorMod(cursor.getAndIncrement(), (long) rows.size());
        return new Row(index, rows.get(i));
    }

    boolean hasColumn(String name) {
        return index.containsKey(name);
    }
}
