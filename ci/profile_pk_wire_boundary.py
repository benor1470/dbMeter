"""Diagnostic-only attribution for the unchanged dbMeter PostgreSQL prepared read."""
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

from bootstrap import ROOT, RUNTIME, VERSIONS, digest, download
from evidence import CAPABILITY_CHECKS, check_xml, read_run
from live_benchmark import Campaign, SIZES, original_inputs
from profile_prepared_pk import thread_counters
from runner import host_snapshot


PACKET = re.compile(r"^(\d+)\.(\d+) IP 127\.0\.0\.1\.(\d+) > 127\.0\.0\.1\.(\d+): tcp (\d+)$")


def summarize_wire(path, port):
    flows = defaultdict(list)
    with path.open() as packets:
        for line in packets:
            packet = line.strip()
            if not packet:
                continue
            match = PACKET.fullmatch(packet)
            if not match:
                raise ValueError(f"Unexpected tcpdump format {line[:100]!r}")
            seconds, micros, source, target, length = map(int, match.groups())
            if not length:
                continue
            if source == port:
                key, direction = target, "reply"
            elif target == port:
                key, direction = source, "request"
            else:
                raise ValueError("Capture admitted a different port")
            timestamp = seconds * 1000000 + micros
            bursts = flows[key]
            if bursts and timestamp < bursts[-1][2]:
                raise ValueError("Capture timestamps went backward on one flow")
            if bursts and bursts[-1][0] == direction:
                bursts[-1][2] = timestamp
                bursts[-1][3] += length
            else:
                bursts.append([direction, timestamp, timestamp, length])
    samples = defaultdict(list)
    wire_pairs = 0
    for bursts in flows.values():
        for req, reply, subsequent in zip(bursts, bursts[1:], bursts[2:]):
            if [req[0], reply[0], subsequent[0]] != ["request", "reply", "request"]:
                continue
            wire_pairs += 1
            timings = {
                "server_gap_us":reply[1] - req[2], "server_burst_us":reply[2] - reply[1],
                "jdbc_gap_us":subsequent[1] - reply[2], "client_burst_us":req[2] - req[1],
                "cycle_us":subsequent[1] - req[1], "request_bytes":req[3], "reply_bytes":reply[3],
            }
            for key,value in timings.items():
                samples[key].append(value)
    if wire_pairs < 10000:
        raise ValueError(f"Only {wire_pairs} complete request/reply/next-request turns")
    if len(flows) != 1:
        raise ValueError("The original one-client measurement had multiple TCP flows")
    bursts = next(iter(flows.values()))
    span_us = bursts[-1][2] - bursts[0][1]
    return {"flows":len(flows), "turns":wire_pairs, "span_us":span_us,
            "turns_per_second":wire_pairs * 1000000 / span_us, "metrics":{
        k: {"median":statistics.median(values), "p95":sorted(values)[(len(values) * 95) // 100]}
        for k, values in samples.items()}}


def worker_counters(pid):
    snapshots = {}
    if not pid:
        raise ValueError("Missing live process identity")
    queue = [pid]
    for current in queue:
        task = Path(f"/proc/{current}/task/{current}")
        queue.extend(int(value) for value in (task / "children").read_text().split())
        for tid, stats in thread_counters(current).items():
            stats["affinity"] = sorted(os.sched_getaffinity(int(tid)))
            stats["name"] = (Path(f"/proc/{current}/task/{tid}") / "comm").read_text().strip()
            snapshots[f"{current}/{tid}"] = stats
    return snapshots


def profile(campaign, engine, server_pid, affinity):
    name = f'{engine}-s1000000_pk-1'
    destination = campaign.out / name
    argv = campaign.java('load', engine, ['--only', 's1000000_pk', '--set', 'load.threads=1',
        '--set', 'load.rampUp=5s', '--set', 'load.warmup=15s', '--set', 'load.duration=60s',
        '--node', 'local', '--run-id', name, '--out', str(destination)])
    args = ['taskset', '-c', affinity['jdbc'], *argv]
    before_host = host_snapshot()
    trace_path = campaign.out / f'{name}-wire.txt'
    capture_flags = (f'tcp port {campaign.port[engine]} '
                     'and (ip[2:2] - ((ip[0] & 15) << 2) - ((tcp[12] & 240) >> 2)) != 0')
    with (campaign.out / f'{name}.log').open('wb') as load_log:
        with subprocess.Popen(args, cwd=ROOT, stdout=load_log, stderr=subprocess.STDOUT) as load:
            try:
                time.sleep(25)
                if load.poll() is not None:
                    raise RuntimeError('JDBC process ended before capture')
                before = {"server":worker_counters(server_pid), "jdbc":worker_counters(load.pid)}
                with trace_path.open('wb') as wire, (campaign.out / f'{name}-tcpdump.log').open('wb') as log:
                    capture = subprocess.run(['sudo','timeout','--signal=INT','--kill-after=2','4',
                        'taskset','-c',affinity['tracer'],'tcpdump','-i','lo','-B','16384','-s','96',
                        '-nn','-tt','-q','-l',capture_flags],
                        stdout=wire, stderr=log, timeout=10)
                after = {"server":worker_counters(server_pid), "jdbc":worker_counters(load.pid)}
                returncode = load.wait(timeout=90)
            finally:
                if load.poll() is None:
                    load.terminate()
    diagnostic = (campaign.out / f'{name}-tcpdump.log').read_text()
    if capture.returncode != 124 or not re.search(r'^0 packets dropped by kernel$', diagnostic, re.M):
        raise RuntimeError(f'Capture ended incorrectly or dropped packets: {diagnostic}')
    run = dict(engine=engine, case='s1000000_pk/1', returncode=returncode,
               report=str((destination / 'local.json').relative_to(campaign.out)),
               host_before=before_host, host_after=host_snapshot())
    run['result'] = read_run(campaign.out, campaign.report, run)
    run['wire'] = summarize_wire(trace_path, campaign.port[engine])
    for owner in before:
        expected = set(map(int, affinity[owner].split(',')))
        if any(set(thread['affinity']) != expected for blob in (before, after) for thread in blob[owner].values()):
            raise RuntimeError(f'Wrong affinity inherited by {owner} processes')
    run['owners'] = {owner: {tid: {**{k: current[k] - before[owner][tid][k] for k in (
        'user_ticks','kernel_ticks','cpu_ns','runnable_wait_ns','switches')},
        'name': current['name'], 'affinity': current['affinity']}
        for tid,current in after[owner].items() if tid in before[owner]}
        for owner in before}
    return run


def main():
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus) < 4:
        raise RuntimeError('Four allowed logical CPUs are required for fixed application/tracer placement')
    affinity = {'server':','.join(map(str,cpus[:2])), 'jdbc':str(cpus[2]), 'tracer':str(cpus[3])}
    topology = {cpu:(Path(f'/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list')).read_text().strip()
                for cpu in cpus[:4]}
    pg_driver = download(VERSIONS['postgres_jdbc'], ROOT / 'drivers/postgresql-42.7.4.jar')
    (RUNTIME/'dependencies.json').write_text(json.dumps({'postgres_jdbc_sha256':digest(pg_driver)}))
    campaign = Campaign('size-scaling', 'qualification', 1000000)
    campaign.ramsql_cpu_mask = affinity['server']
    campaign.postgres_cpu_flags = ['--cpuset-cpus',affinity['server']]
    campaign.report.update(diagnostic_only=True, merge_allowed=False, affinity=affinity, smt_topology=topology,
                           scope='Short sampled original prepared JDBC/server component attribution; no gate')
    campaign.save()
    if campaign.command('original-generator', [sys.executable, '-I', str(campaign.folder/'gen.py')]):
        raise RuntimeError('Original generator failed')
    campaign.prepare_configs()
    campaign.report['original_input_sha256'] = original_inputs()
    subprocess.run(['docker','pull',VERSIONS['postgres_image']],check=True,timeout=180)
    required = {f's{size}_{kind}' for size in SIZES for kind in ('a_pk','b_filter','c_topn','d_last')}
    try:
        with campaign.running('postgres'):
            checks = campaign.out/'postgres-checks.xml'
            command = campaign.java('check','postgres',['--junit',str(checks)])
            command.remove('--skip-setup')
            if campaign.command('postgres-checks',command):
                raise RuntimeError('Original PostgreSQL checks failed')
            check_xml(checks, required | CAPABILITY_CHECKS)
            campaign.report['checks']['postgres'] = True
            values = json.loads(subprocess.check_output(['docker','inspect',campaign.container]))[0]
            campaign.report['postgres_image_id'] = values['Image']
            campaign.report['runs'].append(profile(campaign, 'postgres', values['State']['Pid'], affinity))
            campaign.save()
        campaign.data_files = [ROOT/f'build/size-scaling/ramsql_load_t_{size}.sql' for size in SIZES]
        campaign.check_engine('ramsql')
        check_xml(campaign.out/'ramsql-checks.xml',required)
        check_xml(campaign.out/'ramsql-capabilities.xml',CAPABILITY_CHECKS)
        with campaign.running('ramsql') as child:
            for path in campaign.data_files:
                campaign.psql(path)
            checks = campaign.out/'fresh-ramsql-checks.xml'
            if campaign.command('fresh-ramsql-checks',campaign.java('check','ramsql',['--junit',str(checks)])):
                raise RuntimeError('Original RAMSQL checks failed after fresh load')
            check_xml(checks, required)
            campaign.report['runs'].append(profile(campaign, 'ramsql', child.pid, affinity))
        campaign.report['completed'] = True
        campaign.save()
    finally:
        if campaign.pg_created:
            subprocess.run(['docker','rm','-f',campaign.container],check=False,stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
