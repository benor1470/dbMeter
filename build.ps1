# Builds build\dbmeter.jar with the JDK only (Java 11+). No external dependencies.
# Also builds build\irdb-jdbc.jar (JDBC adapter for InMemory.Net IRDB) when drivers\jirdb-*.jar is present.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$classes = Join-Path $root 'build\classes'
if (Test-Path $classes) { Remove-Item -Recurse -Force $classes }
New-Item -ItemType Directory -Force $classes | Out-Null
$sources = Get-ChildItem -Recurse -Filter *.java (Join-Path $root 'src\main\java') | ForEach-Object { $_.FullName }
& javac --release 11 -encoding UTF-8 -Xlint:all,-serial -d $classes @sources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& jar --create --file (Join-Path $root 'build\dbmeter.jar') --main-class dbmeter.Main -C $classes .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "built $(Join-Path $root 'build\dbmeter.jar')"

$jirdb = Get-ChildItem (Join-Path $root 'drivers') -Filter 'jirdb-*.jar' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($jirdb) {
    $irdbClasses = Join-Path $root 'build\irdb-classes'
    if (Test-Path $irdbClasses) { Remove-Item -Recurse -Force $irdbClasses }
    New-Item -ItemType Directory -Force $irdbClasses | Out-Null
    $irdbSources = Get-ChildItem -Recurse -Filter *.java (Join-Path $root 'irdb-jdbc\src\main\java') | ForEach-Object { $_.FullName }
    & javac --release 11 -encoding UTF-8 -Xlint:all,-serial -cp $jirdb.FullName -d $irdbClasses @irdbSources
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Copy-Item -Recurse (Join-Path $root 'irdb-jdbc\src\main\resources\*') $irdbClasses
    & jar --create --file (Join-Path $root 'build\irdb-jdbc.jar') -C $irdbClasses .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "built $(Join-Path $root 'build\irdb-jdbc.jar') against $($jirdb.Name)"
}
