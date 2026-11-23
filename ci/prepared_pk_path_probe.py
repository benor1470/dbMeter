"""Prove the unchanged JDBC workload reaches the exact candidate's PK executor."""
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from bootstrap import ROOT, RUNTIME, VERSIONS, digest, download

MARKER = "DBMETER_PREPARED_PK_EXECUTOR_HIT"
CASE = "s100000_pk"


def main():
    out = ROOT / "reports" / "prepared-pk-focused"
    out.mkdir(parents=True, exist_ok=False)
    product = next((RUNTIME / "source").iterdir())
    binary = RUNTIME / "ramsql/bin/ramsql"
    provenance = json.loads((RUNTIME / "ramsql-provenance.json").read_text())
    if digest(binary) != provenance["binary_sha256"] or provenance["source"] != VERSIONS["ramsql"]:
        raise ValueError("Prepared-PK check did not receive the pinned candidate")
    with (out / "wire.log").open("w") as evidence:
        subprocess.run([sys.executable, str(product / "tools/test_server.py"), "--binary", str(binary),
                        "--workers", "2"], stdout=evidence, stderr=subprocess.STDOUT, check=True, timeout=180)

    driver = download(VERSIONS["postgres_jdbc"], ROOT / "drivers/postgresql-42.7.4.jar")
    subprocess.run(["sh", "build.sh"], cwd=ROOT, check=True, timeout=60)
    elf = subprocess.check_output(["readelf", "-h", str(binary)], text=True)
    if not re.search(r"Type:\s+EXEC\b", elf):
        raise ValueError("PK breakpoint requires an executable with fixed ELF addresses")
    symbols = subprocess.check_output(["nm", "--defined-only", str(binary)], text=True)
    matches = [line.split(maxsplit=2)[0] for line in symbols.splitlines()
               if line.endswith(".ReadContext.runCachedPrimaryKey")]
    if len(matches) != 1:
        raise ValueError("Exact prepared PK executor address was not found uniquely")
    script = out / "trace.gdb"
    script.write_text(
        "set pagination off\nset confirm off\nset print thread-events off\n"
        "set debuginfod enabled off\nset disable-randomization off\n"
        f"break *0x{matches[0]}\ncommands\nsilent\nprintf \"{MARKER}\\n\"\n"
        "disable 1\ncontinue\nend\nrun\n", encoding="utf-8"
    )
    sys.path.insert(0, str(product / "tools"))
    from test_server import connection

    properties = ROOT / "examples/size-scaling/ramsql.properties"
    original_properties = properties.read_text()
    probe_result = out / "jdbc"
    server_log = out / "server.log"
    with server_log.open("w") as output:
        process = subprocess.Popen(
            ["gdb", "-q", "-nx", "--batch", "-x", str(script), "--args", str(binary),
             "--serve", "--memory", "--insecure-local", "--protocol", "postgres",
             "--port", "0", "--max-clients", "8", "--workers", "2"],
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                banner = re.search(r"^RAMSQL postgres 127\.0\.0\.1:(\d+)$", server_log.read_text(), re.M)
                if banner:
                    break
                if process.poll() is not None:
                    raise RuntimeError("RAMSQL debugger exited before listening")
                time.sleep(0.1)
            else:
                raise RuntimeError("RAMSQL debugger did not report its port")
            port = int(banner[1])
            endpoint = SimpleNamespace(raw=lambda: socket.create_connection(("127.0.0.1", port), 5))
            with connection(endpoint) as pg:
                pg.ok("CREATE TABLE t_100000 (id INT PRIMARY KEY,grp INT NOT NULL,val INT NOT NULL,name TEXT NOT NULL)")
                pg.ok("INSERT INTO t_100000 VALUES (1,1,7919,'row-1')")
            args = ["java", "-Xms32m", "-Xmx256m", "-jar", str(ROOT / "build/dbmeter.jar"),
                    "load", "--skip-setup", "-c", str(properties), "--set", "run.databaseLabel=ramsql",
                    "--set", "load.threads=1", "--set", "load.warmup=0s", "--set", "load.duration=2s",
                    "--set", "load.rampUp=0s", "--only", CASE, "--node", "local",
                    "--run-id", "jdbc-path-check", "--out", str(probe_result)]
            with (out / "jdbc.log").open("w") as evidence:
                subprocess.run(args, cwd=ROOT, env=dict(os.environ, RAMSQL_PORT=str(port)),
                               stdout=evidence, stderr=subprocess.STDOUT, check=True, timeout=45)
            if MARKER not in server_log.read_text().splitlines():
                raise RuntimeError("Original JDBC scenario did not reach the fused prepared PK executor")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
    result = {
        "schema": 1, "binary_sha256": provenance["binary_sha256"], "source": provenance["source"]["revision"],
        "scenario": CASE, "sql_property": next(line for line in original_properties.splitlines()
                                              if line.startswith(f"scenario.{CASE}.sql=")),
        "properties_sha256": digest(properties), "driver_sha256": digest(driver),
        "executor_hit": True, "timing_eligible": False,
    }
    (out / "proof.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
