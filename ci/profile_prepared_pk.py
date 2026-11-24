"""Profile RAMSQL and native PostgreSQL with stock JDBC on the same runner."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from bootstrap import ROOT, RUNTIME, VERSIONS, digest, download
from evidence import CAPABILITY_CHECKS, check_xml, read_run
from live_benchmark import Campaign, SIZES, original_inputs
from loopback_transport import isolated_cgroup, verify
from qualify_loopback_lookup import LoopbackCampaign
from runner import host_snapshot


def thread_counters(pid):
    result = {}
    try:
        threads = list((Path('/proc') / str(pid) / 'task').iterdir())
    except FileNotFoundError:
        return result
    for thread in threads:
        try:
            stat = (thread / 'stat').read_text().rsplit(')', 1)[1].split()
            cpu, wait, switches = map(int, (thread / 'schedstat').read_text().split())
            status = (thread / 'status').read_text().splitlines()
        except FileNotFoundError:
            # JVM helper threads can exit between enumeration and reading.
            continue
        context = {key: int(value) for key, _, value in (line.partition(':') for line in status)
                   if key in ('voluntary_ctxt_switches', 'nonvoluntary_ctxt_switches')}
        result[thread.name] = dict(user_ticks=int(stat[11]), kernel_ticks=int(stat[12]),
                                   cpu_ns=cpu, runnable_wait_ns=wait, switches=switches, **context)
    return result


def thread_deltas(before, after):
    return {tid: {key: value - before[tid][key] for key, value in counters.items()}
            for tid, counters in after.items() if tid in before}


def server_pids(campaign, engine, child):
    if engine == 'ramsql':
        return [child.pid]
    rows = subprocess.check_output(['docker', 'top', campaign.container, '-eo', 'pid'], text=True, timeout=10)
    pids = [int(row.strip()) for row in rows.splitlines()[1:]]
    if len(pids) < 2:
        raise RuntimeError('Native PostgreSQL process set was incomplete')
    return pids


def group_counters(pids):
    return {tid: counters for pid in pids for tid, counters in thread_counters(pid).items()}


def reactor_affinity(server, workers, pin):
    allowed = os.sched_getaffinity(server.pid)
    task = Path('/proc') / str(server.pid) / 'task'
    reactors = sorted(int(thread.name) for thread in task.iterdir() if int(thread.name) != server.pid)
    if len(reactors) != workers or any(os.sched_getaffinity(tid) != allowed for tid in reactors):
        raise RuntimeError('Unexpected worker topology on the fresh server')
    if pin:
        physical = {}
        for cpu in sorted(allowed):
            siblings = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology/core_cpus_list').read_text().strip()
            physical.setdefault(siblings, cpu)
        if len(physical) < workers:
            raise RuntimeError('Cannot place the reactors on distinct cores within the runner affinity')
        for tid, cpu in zip(reactors, list(physical.values())[:workers], strict=True):
            os.sched_setaffinity(tid, {cpu})
            if os.sched_getaffinity(tid) != {cpu}:
                raise RuntimeError('Linux did not apply the requested reactor affinity')
    return {tid: sorted(os.sched_getaffinity(tid)) for tid in reactors}


def profile(campaign, engine, child, client_count, perf, deployment):
    name = f'{engine}-{deployment}-s1000000_pk-{client_count}'
    destination = campaign.out / name
    argv = campaign.java('load', engine, ['--only', 's1000000_pk', '--set', f'load.threads={client_count}',
        '--set', 'load.rampUp=5s', '--set', 'load.warmup=15s', '--set', 'load.duration=60s',
        '--node', 'local', '--run-id', name, '--out', str(destination)])
    before = host_snapshot()
    trace = Path(os.environ['RUNNER_TEMP']) / f'{name}.perf.data'
    with (campaign.out / (name + '.log')).open('wb') as load_log, \
         (campaign.out / (name + '-perf.log')).open('wb') as perf_log:
        with subprocess.Popen(argv, cwd=ROOT, stdout=load_log, stderr=subprocess.STDOUT) as load:
            try:
                # The original warmup and ramp complete before sampling.
                time.sleep(25)
                if load.poll() is not None:
                    raise RuntimeError('JDBC load ended before CPU sampling')
                pids = server_pids(campaign, engine, child)
                threads_before = group_counters(pids)
                jdbc_before = thread_counters(load.pid)
                sample_start_ns = time.time_ns()
                subprocess.run(['sudo', perf, 'record', '--quiet', '--event', 'cpu-clock', '--freq', '199',
                    '--call-graph', 'dwarf,4096', '--pid', ','.join(map(str, [*pids, load.pid])), '--output', str(trace),
                    '--', 'sleep', '30'], stdout=perf_log, stderr=subprocess.STDOUT, check=True, timeout=45)
                sample_end_ns = time.time_ns()
                threads_after = group_counters(pids)
                jdbc_after = thread_counters(load.pid)
                if load.poll() is not None:
                    raise RuntimeError('JDBC load ended before the sample interval completed')
                returncode = load.wait(timeout=90)
            finally:
                if load.poll() is None:
                    load.kill()
    run = dict(engine=engine, deployment=deployment, case=f's1000000_pk/{client_count}', returncode=returncode,
               report=str((destination / 'local.json').relative_to(campaign.out)),
               host_before=before, host_after=host_snapshot())
    run['result'] = read_run(campaign.out, campaign.report, run)
    if engine == 'ramsql':
        run['workers'] = campaign.report['ramsql_machine_capacity']['read_workers']
    run['thread_deltas'] = thread_deltas(threads_before, threads_after)
    run['jdbc_thread_deltas'] = thread_deltas(jdbc_before, jdbc_after)
    run['sample_epoch_ns'] = [sample_start_ns, sample_end_ns]
    for kind, options in [('self', ['--no-children']), ('inclusive', ['--children'])]:
        with (campaign.out / f'{name}-{kind}.txt').open('wb') as report:
            subprocess.run(['sudo', perf, 'report', '--stdio', '--input', str(trace),
                            '--sort', 'comm,dso,symbol', '--percent-limit', '0.2', *options],
                           stdout=report, check=True, timeout=40)
    return run


class ProfileCampaign(LoopbackCampaign):
    mapped = False

    @contextmanager
    def running(self, engine):
        selected = super().running(engine) if self.mapped else Campaign.running(self, engine)
        with selected as server:
            yield server


def main(perf, probe):
    pg_driver = download(VERSIONS['postgres_jdbc'], ROOT / 'drivers/postgresql-42.7.4.jar')
    (RUNTIME / 'dependencies.json').write_text(json.dumps({'postgres_jdbc_sha256': digest(pg_driver)}))
    campaign = ProfileCampaign('size-scaling', 'qualification', 1000000)
    if (campaign.ram_provenance['source'] != VERSIONS['ramsql'] or
            digest(campaign.binary) != VERSIONS['ramsql']['release']['image_sha256']):
        raise ValueError('The profile requires the exact published RAMSQL binary')
    campaign.probe = probe
    probe_sha256 = probe.with_suffix('.sha256').read_text().strip()
    if digest(probe) != probe_sha256:
        raise ValueError('Socket-map probe differs from the qualified artifact')
    campaign.report.update(diagnostic_only=True, merge_allowed=False, profiling_tool=Path(perf).name,
                           probe_sha256=probe_sha256,
                           scope='CPU and scheduler samples for two reactors with and without distinct-core placement')
    campaign.save()
    generator = campaign.folder / 'gen.py'
    if campaign.command('original-generator', [sys.executable, '-I', str(generator)]):
        raise RuntimeError('Original size-scaling generator failed')
    campaign.prepare_configs()
    campaign.report['original_input_sha256'] = original_inputs()
    subprocess.run(['docker', 'pull', VERSIONS['postgres_image']], check=True, timeout=180)
    required = {f's{size}_{kind}' for size in SIZES for kind in ('a_pk', 'b_filter', 'c_topn', 'd_last')}
    with campaign.running('postgres'):
        checks = campaign.out / 'postgres-checks.xml'
        command = campaign.java('check', 'postgres', ['--junit', str(checks)])
        command.remove('--skip-setup')
        if campaign.command('postgres-checks', command):
            raise RuntimeError('Original native PostgreSQL correctness checks failed')
        check_xml(checks, required | CAPABILITY_CHECKS)
        campaign.report['runs'].append(profile(campaign, 'postgres', None, 8, perf, 'tcp'))
        campaign.save()
    campaign.data_files = [ROOT / f'build/size-scaling/ramsql_load_t_{size}.sql' for size in SIZES]
    campaign.check_engine('ramsql')
    campaign.save()
    check_xml(campaign.out / 'ramsql-checks.xml', required)
    check_xml(campaign.out / 'ramsql-capabilities.xml', CAPABILITY_CHECKS)
    with isolated_cgroup() as campaign.cgroup:
        campaign.report['loopback_wire_proof'] = verify(probe, campaign.cgroup, campaign.port['ramsql'],
                                                       ('duplex',), campaign.out)
        workers = 2
        campaign.worker_limit = workers
        campaign.mapped = True
        for trial, pin in enumerate((False, True, False), 1):
            placement = 'separate-cores' if pin else 'automatic'
            deployment = f'reply-cache-{placement}-{trial}'
            with campaign.running('ramsql') as child:
                if campaign.report['ramsql_machine_capacity']['read_workers'] != workers:
                    raise RuntimeError('RAMSQL did not admit the requested worker budget')
                affinity = reactor_affinity(child, workers, pin)
                for path in campaign.data_files:
                    campaign.psql(path)
                name = f'{deployment}-profile-checks'
                checks = campaign.out / (name + '.xml')
                if campaign.command(name, campaign.java('check', 'ramsql', ['--junit', str(checks)])):
                    raise RuntimeError('Original size-scaling correctness checks failed: ' + deployment)
                check_xml(checks, required)
                run = profile(campaign, 'ramsql', child, 8, perf, deployment)
                run['reactor_affinity'] = affinity
                campaign.report['runs'].append(run)
                campaign.save()
    campaign.report['completed'] = True
    campaign.save()


if __name__ == '__main__':
    main(sys.argv[1], Path(sys.argv[2]).resolve(strict=True))
