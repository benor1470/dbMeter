"""Isolated Linux socket-map diagnostics. No database or JDBC modifications."""
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import uuid


def membership(pid):
    return next(line[3:] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines()
                if line.startswith('0::'))


@contextmanager
def isolated_cgroup():
    root = Path('/sys/fs/cgroup')
    pid = os.getpid()
    original = root / membership(pid).lstrip('/')
    path = root / ('dbmeter-loopback-' + uuid.uuid4().hex)
    subprocess.run(['sudo', '-n', 'mkdir', str(path)], check=True, timeout=10)

    def move(destination):
        subprocess.run(['sudo', '-n', 'tee', str(destination / 'cgroup.procs')],
                       input=f'{pid}\n', text=True, stdout=subprocess.DEVNULL, check=True, timeout=10)

    try:
        move(path)
        if membership(pid) != f'/{path.name}':
            raise RuntimeError('Benchmark process did not enter the dedicated cgroup')
        yield path
    finally:
        move(original)
        subprocess.run(['sudo', '-n', 'rmdir', str(path)], check=True, timeout=10)


@contextmanager
def attachment(probe, cgroup, port, direction, log_file):
    with open(log_file, 'wb') as log:
        with subprocess.Popen(['sudo', '-n', str(probe), str(cgroup), str(port), direction],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log) as process:
            try:
                if not select.select([process.stdout], [], [], 15)[0] or process.stdout.readline() != b'ready\n':
                    raise RuntimeError(f'Linux socket map did not attach. See {log_file}')
                yield
            finally:
                process.stdin.close()
                try:
                    code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    raise
                if code:
                    raise RuntimeError(f'Linux socket map exited with {code}. See {log_file}')


def segments(sock):
    info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 256)
    # Linux uapi struct tcp_info.tcpi_data_segs_out.
    return struct.unpack_from('=I', info, 156)[0]


def receive(sock, expected):
    data = bytearray()
    while len(data) < len(expected):
        fragment = sock.recv(len(expected) - len(data))
        if not fragment:
            raise AssertionError('Short TCP stream')
        data.extend(fragment)
    if data != expected:
        raise AssertionError('The TCP byte stream was reordered or corrupted')


def stream(port, family, address='127.0.0.1'):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.settimeout(5)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((address, port))
        listener.listen(1)
        with socket.socket(family, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect((address if family == socket.AF_INET else '::ffff:' + address, port))
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(5)
                for endpoint in (client, peer):
                    endpoint.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                for turn in range(32):
                    request = bytes([turn]) * 41
                    client.sendall(request[:12])
                    client.sendall(request[12:])
                    receive(peer, request)
                    reply = request * 3
                    peer.sendall(reply[:10])
                    peer.sendall(reply[10:])
                    receive(client, reply)
                counts = [segments(client), segments(peer)]
                peer.shutdown(socket.SHUT_WR)
                if client.recv(1) != b'':
                    raise AssertionError('TCP end of stream was lost')
                return counts


def verify(probe, cgroup, port, directions, folder):
    proof = {}
    for direction in ('control', *directions):
        lease = nullcontext() if direction == 'control' else attachment(
            probe, cgroup, port, direction, folder / f'wire-{direction}.log')
        with lease:
            for family in (socket.AF_INET, socket.AF_INET6):
                counts = stream(port, family)
                expected_bypass = [direction == 'duplex', direction != 'control']
                proof[f'{direction}/{family.name}'] = counts
                (folder / 'wire.json').write_text(json.dumps(proof, indent=2) + '\n')
                if [count == 0 for count in counts] != expected_bypass:
                    raise RuntimeError(f'{direction}/{family.name}: unexpected TCP data segments {counts}')
            if direction != 'control' and 0 in stream(port, socket.AF_INET, '127.0.0.2'):
                raise RuntimeError('The socket map affected an address outside its scope')
    return proof


if __name__ == '__main__':
    binary = Path(sys.argv[1]).resolve(strict=True)
    evidence = Path(sys.argv[2])
    evidence.mkdir(parents=True, exist_ok=True)
    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1', 0))
        selected_port = port_probe.getsockname()[1]
    with isolated_cgroup() as group:
        print(verify(binary, group, selected_port, ('replies', 'duplex'), evidence))
