# Runs the table-size scaling benchmark on PostgreSQL, InMemory.Net IRDB and RAMSQL.
# Everything lands in reports/size-scaling (logs, JUnit check results, per-run reports, comparisons,
# results.json for the HTML report).
#
# Needs running servers: PostgreSQL (PGPASSWORD, default 1), IRDB on 5060, and a FRESHLY STARTED RAMSQL
# on 5433 (ramsql.exe --memory --insecure-local --port 5433 --rows 1000000), plus build/irdb-jdbc.jar (build.ps1)
# and python. -Databases pg,ramsql skips IRDB; -OutName writes to another reports/ folder.
param([int[]]$Sizes = @(100, 512, 1000, 10000, 100000, 1000000),
      [string[]]$Databases = @('pg', 'irdb', 'ramsql'),
      [string]$OutName = 'size-scaling')
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$here = $PSScriptRoot
$out = Join-Path $root "reports\$OutName"
$runs = Join-Path $out 'runs'
$dbmeter = Join-Path $root 'dbmeter.cmd'
New-Item -ItemType Directory -Force $out, $runs | Out-Null
Set-Location $root

function Step($title) { Write-Host "`n===== $title =====" -ForegroundColor Cyan }
function DbMeter($log, [string[]]$arguments) {
    & $dbmeter @arguments 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath (Join-Path $out $log) | Write-Host
}
$cfg = @{ pg = "$here\pg.properties"; irdb = "$here\irdb.properties"; ramsql = "$here\ramsql.properties" }

Step 'generate'
python -I "$here\gen.py"

Step 'PostgreSQL: build data + checks'
if ($Databases -contains 'pg') {
    DbMeter 'checks-pg.txt' @('check', '-c', $cfg.pg, '--junit', "$out\checks-pg.xml")
}

Step 'IRDB: copy data from PostgreSQL + checks'
if ($Databases -contains 'irdb') {
    $tables = ($Sizes | ForEach-Object { "t_$_" }) -join ','
    & java -Xmx4g -cp "build\irdb-jdbc.jar;drivers\jirdb-2025-02-02.jar;drivers\fastutil-8.5.15.jar;drivers\postgresql-42.7.4.jar" `
        dbmeter.irdb.CopyToIrdb --source jdbc:postgresql://localhost:5432/postgres --source-user postgres `
        --source-password $(if ($env:PGPASSWORD) { $env:PGPASSWORD } else { '1' }) --source-schema dbmeter_size `
        --target jdbc:irdb://localhost:5060/size_bench --tables $tables 2>&1 |
        ForEach-Object { "$_" } | Where-Object { $_ -notmatch '(Reading|Writing) Table' } |
        Tee-Object -FilePath "$out\copy-irdb.txt" | Write-Host
    DbMeter 'checks-irdb.txt' @('check', '-c', $cfg.irdb, '--junit', "$out\checks-irdb.xml")
}

Step 'RAMSQL: multi-row INSERT load per size + checks'
if ($Databases -contains 'ramsql') {
    foreach ($n in $Sizes) {
        DbMeter "ramsql-load-$n.txt" @('check', '-c', $cfg.ramsql, '--only', "s${n}_d_last",
            '--set', "check.setup.file=../../build/size-scaling/ramsql_load_t_$n.sql", '--set', 'check.setup.continueOnError=true')
}
DbMeter 'checks-ramsql.txt' @('check', '-c', $cfg.ramsql, '--junit', "$out\checks-ramsql.xml")
}

foreach ($n in $Sizes) {
    Step "load: $n rows"
    foreach ($db in $Databases) {
        DbMeter "load-$db-$n.txt" @('load', '-c', $cfg[$db], '--only', "s${n}_pk,s${n}_filter,s${n}_topn,s${n}_fetch_all",
            '--run-id', "size-$db-$n", '--node', 'local', '--out', "$runs\size-$db-$n")
    }
    DbMeter "compare-$n.txt" (@('compare') + ($Databases | ForEach-Object { "$runs\size-$_-$n" }) + @('--out', "$out\compare-$n"))
}

Step 'isolated: one query kind at a time (mixed runs are paced by their slowest query)'
foreach ($n in $Sizes) {
    foreach ($db in $Databases) {
        foreach ($kind in 'pk', 'filter', 'topn', 'fetch_all') {
            DbMeter "iso-$db-$n-$kind.txt" @('load', '-c', $cfg[$db], '--only', "s${n}_$kind", '--set', 'load.warmup=2s',
                '--set', 'load.duration=5s', '--run-id', "iso-$db-$n-$kind", '--node', 'local', '--out', "$runs\iso-$db-$n-$kind")
        }
    }
}

Step 'connections: 32 concurrent clients'
foreach ($db in $Databases) {
    DbMeter "conn32-$db.txt" @('load', '-c', $cfg[$db], '--only', 's100_pk', '--set', 'load.threads=32', '--set', 'load.warmup=0s',
        '--set', 'load.duration=3s', '--run-id', "conn32-$db", '--node', 'local', '--out', "$runs\conn32-$db")
}

Step 'summary'
python -I "$here\summarize.py" $out
