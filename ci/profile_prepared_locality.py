"""Collect role-scoped Linux scheduling and TCP evidence for original JDBC reads."""
from collections import Counter, defaultdict
import ctypes
import errno
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time

from bootstrap import ROOT
from evidence import read_run
from profile_prepared_pk import main as profile_campaign
from runner import host_snapshot


TRACE_SECONDS = 4
TRACE_EVENTS = ('sched:sched_waking', 'sched:sched_switch', 'sched:sched_migrate_task',
                'syscalls:sys_exit_sendto', 'syscalls:sys_exit_recvfrom')
LINE = re.compile(r'^\s*(\d+)\s+\[(\d+)\]\s+(\d+\.\d+):\s+([\w:]+):\s+(.*)$')
FIELD = re.compile(r'\b(pid|target_cpu|prev_pid|next_pid|prev_state|orig_cpu|dest_cpu)=(\S+)')
TCP_STAT = re.compile(r'\b(data_segs_in|data_segs_out|segs_in|segs_out|bytes_sent|bytes_received):(\d+)')


def topology():
    allowed = sorted(os.sched_getaffinity(0))
    cores = {}
    for cpu in allowed:
        path = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        cores[str(cpu)] = {name: (path / name).read_text().strip()
                           for name in ('physical_package_id', 'core_id', 'thread_siblings_list')}
    return cores


def match_ids(field, ids):
    assert ids
    return '(' + ' || '.join(f'{field} == {tid}' for tid in sorted(ids)) + ')'


def record(perf, trace, participants, server, command):
    owned = match_ids('pid', participants)
    switches = match_ids('prev_pid', participants) + ' || ' + match_ids('next_pid', participants)
    server_calls = match_ids('common_pid', server) + ' && ret > 0'
    filters = (owned, switches, owned, server_calls, server_calls)
    argv = ['sudo', '-n', perf, 'record', '-a', '--quiet', '--no-buildid',
            '--no-buildid-cache', '--clockid', 'mono', '-m', '4096', '-o', str(trace)]
    for event, restriction in zip(TRACE_EVENTS, filters):
        argv += ['-e', event, '--filter', restriction]
    return subprocess.run([*argv, '--', *command], capture_output=True, text=True, timeout=20)


def prepare(campaign, perf):
    """Check the actual event filters before seeding or timing the workload."""
    campaign.report['linux_cpu_topology'] = topology()
    with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as temp:
        results = record(perf, Path(temp) / 'preflight.data', {os.getpid()}, {os.getpid()}, ['sleep', '.1'])
    available = results.returncode == 0
    reason = next((name for name in ('unknown tracepoint', 'Permission denied', 'Operation not permitted',
                   'not mounted', 'Invalid argument', 'failed to set filter')
                   if name.casefold() in results.stderr.casefold()), 'perf record failed')
    # Retain the exact event names and a status, never perf's process metadata.
    campaign.report['tracepoint_preflight'] = dict(available=available, events=TRACE_EVENTS,
        status='ready' if available else reason)
    campaign.save()
    if not available:
        raise RuntimeError(f'Hosted scheduler and TCP tracepoints unavailable: {reason} (exit {results.returncode})')


def thread_roles(server_pid, java_pid, clients):
    server = Path(f'/proc/{server_pid}/task')
    java = Path(f'/proc/{java_pid}/task')
    lookup_workers = []
    for thread in java.iterdir():
        try:
            if (thread / 'comm').read_text().startswith('dbmeter-worker-'):
                lookup_workers.append(int(thread.name))
        except FileNotFoundError:
            continue
    if len(lookup_workers) != clients:
        raise RuntimeError(f'Expected {clients} owned JDBC workload threads, found {len(lookup_workers)}')
    return {**{int(thread.name): 'server' for thread in server.iterdir()},
            **dict.fromkeys(lookup_workers, 'jdbc')}


def socket_counters(port):
    out = subprocess.check_output(['ss', '-Htin', '( sport = :' + str(port) + ' or dport = :' + str(port) + ' )'],
                                  text=True, timeout=5)
    counters, key = {}, None
    for line in out.splitlines():
        if line.startswith('ESTAB'):
            _, _, _, local, peer, *_ = line.split()
            endpoints = []
            for address in (local, peer):
                host, service = address.rsplit(':', 1)
                ip = ipaddress.ip_address(host.strip('[]'))
                endpoints.append(str(getattr(ip, 'ipv4_mapped', None) or ip) + ':' + service)
            key = tuple(endpoints) if all(item.startswith('127.0.0.1:') for item in endpoints) else None
            if key is not None:
                counters[key] = {}
        elif line[:1].isspace() and key is not None:
            counters[key].update({field: int(value) for field, value in TCP_STAT.findall(line)})
    return counters


def socket_deltas(before, after, port, clients):
    result = {}
    if before.keys() != after.keys() or len(before) != 2 * clients:
        raise RuntimeError('Owned TCP connections changed during the scheduler sample')
    for (local, peer), current in after.items():
        role = 'server' if local.endswith(':' + str(port)) else 'jdbc'
        counters = result.setdefault(role, Counter())
        previous = before[local, peer]
        if not {'data_segs_in', 'data_segs_out'}.issubset(previous) or not previous.keys() <= current.keys():
            raise RuntimeError('Linux TCP_INFO did not report stable owned data-segment counters')
        for field, value in current.items():
            delta = value - previous.get(field, 0)
            if delta < 0:
                raise RuntimeError('Owned TCP counter decreased')
            counters[field] += delta
    return result


def receive_cpu_samples(pid, port, until):
    """Read SO_INCOMING_CPU of this harness's server sockets, releasing each duplicate immediately."""
    if platform.machine() != 'x86_64' or not hasattr(os, 'pidfd_open'):
        return {'available': False, 'reason': 'pidfd_getfd unavailable on this diagnostic host'}
    owner = int(os.environ['SUDO_UID'])
    fd_path = Path(f'/proc/{pid}/fd')
    if fd_path.parent.stat().st_uid != owner:
        raise RuntimeError('Refusing to inspect a process outside the harness account')
    syscall = ctypes.CDLL(None, use_errno=True).syscall
    syscall.restype = ctypes.c_long
    try:
        pidfd = os.pidfd_open(pid)
        try:
            by_socket = defaultdict(Counter)
            socket_fds = [int(fd.name) for fd in fd_path.iterdir() if os.readlink(fd).startswith('socket:[')]
            while time.monotonic() < until:
                for fd in socket_fds:
                    duplicate = syscall(438, pidfd, fd, 0)  # Linux x86_64 pidfd_getfd
                    if duplicate == -1:
                        code = ctypes.get_errno()
                        if code == errno.EBADF:
                            continue
                        raise OSError(code, os.strerror(code))
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM, fileno=duplicate) as accepted:
                        if accepted.getsockname() == ('127.0.0.1', port):
                            try:
                                peer = accepted.getpeername()
                            except OSError:
                                continue
                            if peer[0] == '127.0.0.1':
                                option = getattr(socket, 'SO_INCOMING_CPU', 49)
                                by_socket[fd][accepted.getsockopt(socket.SOL_SOCKET, option)] += 1
                time.sleep(.05)
            if not by_socket:
                return {'available': False, 'reason': 'no owned connected sockets sampled'}
            return {'available': True, 'connections': [
                {'samples': sum(counts.values()), 'cpu_counts': counts}
                for counts in by_socket.values()]}
        finally:
            os.close(pidfd)
    except OSError as error:
        return {'available': False, 'reason': errno.errorcode.get(error.errno, 'socket sampling denied')}


def location(cores, origin, destination):
    if origin == destination:
        return 'same_logical_cpu'
    if str(origin) not in cores or str(destination) not in cores:
        return 'unknown'
    origin, destination = cores[str(origin)], cores[str(destination)]
    keys = ('physical_package_id', 'core_id')
    return 'same_physical_core' if all(origin[key] == destination[key] for key in keys) else 'different_core'


def distribution(values):
    values.sort()
    return {'count': len(values), **{f'p{p}_us': values[(len(values) - 1) * p // 100] / 1000
                                    for p in (50, 95, 99, 100)}}


def summarize(lines, roles, cores):
    pending = {}
    handoffs = defaultdict(list)
    counters = Counter()
    runnable_by_role = defaultdict(Counter)
    lost = 0
    first_ns = last_ns = None
    for line in lines:
        if 'PERF_RECORD_LOST' in line:
            lost += 1
        sample = LINE.match(line)
        if sample is None:
            continue
        tid, cpu, timestamp, event, payload = sample.groups()
        tid, cpu = int(tid), int(cpu)
        second, fraction = timestamp.split('.')
        now = int(second) * 1_000_000_000 + int(fraction.ljust(9, '0'))
        if first_ns is None:
            first_ns = now
        last_ns = now
        fields = dict(FIELD.findall(payload))
        if event == 'sched:sched_waking':
            target = int(fields['pid'])
            if target in roles:
                pending[target] = (roles.get(tid, 'external_or_kernel'), cpu, now)
                counters[f'{roles[target]}_wakes'] += 1
        elif event == 'sched:sched_switch':
            previous, following = int(fields['prev_pid']), int(fields['next_pid'])
            if previous in roles:
                change = 'runnable' if fields['prev_state'].startswith('R') else 'blocked'
                runnable_by_role[roles[previous]][change] += 1
            if following in roles:
                counters[f'{roles[following]}_switches_in'] += 1
            if following in pending:
                source, waker_cpu, wake_ns = pending.pop(following)
                if now >= wake_ns:
                    region = location(cores, waker_cpu, cpu)
                    handoffs[f'{source}_to_{roles[following]}|{region}'].append(now - wake_ns)
        elif event == 'sched:sched_migrate_task':
            target = int(fields['pid'])
            if target in roles:
                region = location(cores, int(fields['orig_cpu']), int(fields['dest_cpu']))
                counters[f'{roles[target]}_migrations|{region}'] += 1
        elif tid in roles and event in TRACE_EVENTS[3:]:
            counters['server_' + event.removeprefix('syscalls:sys_exit_')] += 1
    if lost:
        raise RuntimeError(f'Perf dropped scheduler records ({lost} loss records)')
    if not all(counters[key] for key in ('server_switches_in', 'jdbc_switches_in', 'server_sendto', 'server_recvfrom')):
        raise RuntimeError('Required owned scheduler/TCP activity was absent or unparseable')
    return {'event_window_ns': last_ns - first_ns, 'counts': counters, 'switch_out': runnable_by_role,
            'wake_to_run': {group: distribution(values) for group, values in handoffs.items()}}


def decode(perf, trace, roles, cores):
    args = ['sudo', '-n', perf, 'script', '--input', str(trace), '--ns', '--show-lost-events',
            '-F', 'tid,cpu,time,event,trace']
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as script:
        try:
            result = summarize(script.stdout, roles, cores)
            if script.wait(timeout=30):
                raise RuntimeError('Perf could not decode owned scheduler/TCP events')
            return result
        finally:
            if script.poll() is None:
                script.kill()


def normalize_tcp(trace, report_path, start, end):
    """The existing JDBC timeline has one-second buckets, so rates are estimates."""
    scenario = json.loads(report_path.read_text())['scenarios']['s1000000_pk']
    second_ns = 1_000_000_000
    operations = 0
    for second, values in scenario['timeline'].items():
        left = int(second) * second_ns
        overlap = max(0, min(end, left + second_ns) - max(start, left))
        operations += (values[0] - values[1]) * overlap / second_ns
    if operations <= 0:
        raise RuntimeError('Owned trace window did not overlap the unchanged JDBC measurement')
    trace['normalization'] = dict(
        method='overlap-weighted original one-second JDBC timeline buckets',
        successful_jdbc_operations_estimate=operations,
        server_successful_sends_per_operation_estimate=trace['counts']['server_sendto'] / operations,
        data_segments_per_operation_estimate={
            role: {kind: counters[kind] / operations for kind in ('data_segs_in', 'data_segs_out')}
            for role, counters in trace['tcp_info_deltas'].items()})


def capture(campaign, server, clients, perf):
    name = f's1000000_pk-{clients}'
    destination = campaign.out / name
    port = campaign.port['ramsql']
    argv = campaign.java('load', 'ramsql', ['--only', 's1000000_pk', '--set', f'load.threads={clients}',
        '--set', 'load.rampUp=5s', '--set', 'load.warmup=15s', '--set', 'load.duration=60s',
        '--node', 'local', '--run-id', name, '--out', str(destination)])
    host_before = host_snapshot()
    with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as temp:
        trace = Path(temp) / 'owned.data'
        with (campaign.out / (name + '.log')).open('wb') as log, \
             subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT) as load:
            try:
                time.sleep(25)  # sample inside the unchanged measured phase
                if load.poll() is not None:
                    raise RuntimeError('JDBC load ended before scheduler capture')
                roles = thread_roles(server.pid, load.pid, clients)
                server_ids = {tid for tid, role in roles.items() if role == 'server'}
                before = socket_counters(port)
                wall_start = time.time_ns()
                record_result = record(perf, trace, roles, server_ids, [sys.executable, str(Path(__file__).resolve()),
                                       '--receive-cpus', str(server.pid), str(port)])
                wall_end = time.time_ns()
                after = socket_counters(port)
                if load.poll() is not None:
                    raise RuntimeError('JDBC load ended during scheduler capture')
                status = load.wait(timeout=90)
            finally:
                if load.poll() is None:
                    load.kill()
        run = dict(engine='ramsql', case=f's1000000_pk/{clients}', returncode=status,
                   report=str((destination / 'local.json').relative_to(campaign.out)),
                   host_before=host_before, host_after=host_snapshot())
        run['result'] = read_run(campaign.out, campaign.report, run)
        try:
            if record_result.returncode:
                raise RuntimeError(f'Owned scheduler/TCP recording failed (exit {record_result.returncode})')
            run['trace'] = decode(perf, trace, roles, campaign.report['linux_cpu_topology'])
            run['trace']['receive_cpu'] = json.loads(record_result.stdout)
            run['trace']['tcp_info_deltas'] = socket_deltas(before, after, port, clients)
            run['trace']['tcp_info_window_unix_ns'] = [wall_start, wall_end]
            normalize_tcp(run['trace'], destination / 'local.json', wall_start, wall_end)
        except Exception:
            campaign.report['runs'].append(run)
            campaign.save()
            raise
        return run


if __name__ == '__main__':
    if sys.argv[1] == '--receive-cpus':
        deadline = time.monotonic() + TRACE_SECONDS
        result = receive_cpu_samples(*map(int, sys.argv[2:]), deadline)
        time.sleep(max(0, deadline - time.monotonic()))
        print(json.dumps(result))
    else:
        profile_campaign(sys.argv[1], capture=capture, prepare=prepare,
                         scope='Owned Linux scheduler and TCP diagnostics for original prepared JDBC reads')
