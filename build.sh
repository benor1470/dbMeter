#!/bin/sh
# Builds build/dbmeter.jar with the JDK only (Java 11+). No external dependencies.
# Also builds build/irdb-jdbc.jar (JDBC adapter for InMemory.Net IRDB) when drivers/jirdb-*.jar is present.
set -e
root=$(cd "$(dirname "$0")" && pwd)
rm -rf "$root/build/classes"
mkdir -p "$root/build/classes"
find "$root/src/main/java" -name '*.java' > "$root/build/sources.txt"
javac --release 11 -encoding UTF-8 -Xlint:all,-serial -d "$root/build/classes" @"$root/build/sources.txt"
jar --create --file "$root/build/dbmeter.jar" --main-class dbmeter.Main -C "$root/build/classes" .
echo "built $root/build/dbmeter.jar"

jirdb=$(ls "$root"/drivers/jirdb-*.jar 2>/dev/null | head -n 1)
if [ -n "$jirdb" ]; then
    rm -rf "$root/build/irdb-classes"
    mkdir -p "$root/build/irdb-classes"
    find "$root/irdb-jdbc/src/main/java" -name '*.java' > "$root/build/irdb-sources.txt"
    javac --release 11 -encoding UTF-8 -Xlint:all,-serial -cp "$jirdb" -d "$root/build/irdb-classes" @"$root/build/irdb-sources.txt"
    cp -R "$root/irdb-jdbc/src/main/resources/." "$root/build/irdb-classes"
    jar --create --file "$root/build/irdb-jdbc.jar" -C "$root/build/irdb-classes" .
    echo "built $root/build/irdb-jdbc.jar against $(basename "$jirdb")"
fi
