"""Qualify stock PostgreSQL TLS against the Linux socket-map deployment."""
from contextlib import nullcontext
from pathlib import Path
import socket
import subprocess
import sys

from loopback_transport import attachment, isolated_cgroup, verify


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def qualify(source, binary, probe, scratch):
    evidence = scratch / "evidence"
    with isolated_cgroup() as group:
        verify(probe, group, available_port(), ("duplex",), evidence)
        for label in ("tcp", "indexed-first", "indexed-repeat"):
            for workers in (2, 4):
                name = f"{label}-{workers}"
                port = available_port()
                lease = (nullcontext() if label == "tcp" else
                         attachment(probe, group, port, "duplex", evidence / f"{name}.log"))
                with lease:
                    subprocess.run([
                        sys.executable, "tools/test_postgres_tls.py",
                        "--binary", str(binary), "--workers", str(workers), "--port", str(port),
                        "--scratch", str(scratch / name), "--report", str(evidence / f"{name}.json"),
                    ], cwd=source, check=True, timeout=120)


if __name__ == "__main__":
    qualify(*(Path(argument).resolve(strict=True) for argument in sys.argv[1:]))
