"""Measure stock PostgreSQL exchanges on sibling and separate physical cores."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from bootstrap import ROOT, RUNTIME, VERSIONS, digest, download
from evidence import CAPABILITY_CHECKS, check_xml, read_run
from live_benchmark import Campaign, SIZES, original_inputs
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


def physical_placement():
    allowed = os.sched_getaffinity(0)
    for server in sorted(allowed):
        siblings = set()
        source = Path(f'/sys/devices/system/cpu/cpu{server}/topology/thread_siblings_list')
        for item in source.read_text().strip().split(','):
            first, _, last = item.partition('-')
            siblings.update(range(int(first), int(last or first) + 1))
        same = sorted((siblings & allowed) - {server})
        separate = sorted(allowed - siblings)
        if same and separate:
            return dict(server=server, same=same[0], separate=separate[0])
    raise RuntimeError('This runner does not expose both sibling and separate CPU cores')


def profile(campaign, engine, child, placement, cpu, perf):
    name = f'{engine}-s1000000_pk-{placement}'
    destination = campaign.out / name
    for pid in server_pids(campaign, engine, child):
        subprocess.run(['sudo', 'taskset', '-apc', str(cpu['server']), str(pid)],
                       check=True, stdout=subprocess.DEVNULL, timeout=10)
    argv = ['taskset', '-c', str(cpu[placement]), *campaign.java('load', engine, ['--only', 's1000000_pk', '--set', 'load.threads=1',
        '--set', 'load.rampUp=5s', '--set', 'load.warmup=15s', '--set', 'load.duration=60s',
        '--node', 'local', '--run-id', name, '--out', str(destination)])]
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
                if any(os.sched_getaffinity(pid) != {cpu['server']} for pid in pids) or os.sched_getaffinity(load.pid) != {cpu[placement]}:
                    raise RuntimeError('Server and JDBC did not retain the scheduled placement')
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
    run = dict(engine=engine, case='s1000000_pk/1', placement=placement, returncode=returncode,
               report=str((destination / 'local.json').relative_to(campaign.out)),
               host_before=before, host_after=host_snapshot())
    run['result'] = read_run(campaign.out, campaign.report, run)
    run['thread_deltas'] = thread_deltas(threads_before, threads_after)
    run['jdbc_thread_deltas'] = thread_deltas(jdbc_before, jdbc_after)
    run['sample_epoch_ns'] = [sample_start_ns, sample_end_ns]
    for kind, options in [('self', ['--no-children']), ('inclusive', ['--children'])]:
        with (campaign.out / f'{name}-{kind}.txt').open('wb') as report:
            subprocess.run(['sudo', perf, 'report', '--stdio', '--input', str(trace),
                            '--sort', 'comm,dso,symbol', '--percent-limit', '0.2', *options],
                           stdout=report, check=True, timeout=40)
    return run


def main(perf):
    pg_driver = download(VERSIONS['postgres_jdbc'], ROOT / 'drivers/postgresql-42.7.4.jar')
    (RUNTIME / 'dependencies.json').write_text(json.dumps({'postgres_jdbc_sha256': digest(pg_driver)}))
    campaign = Campaign('size-scaling', 'qualification', 1000000)
    cpu = physical_placement()
    campaign.report.update(diagnostic_only=True, merge_allowed=False, profiling_tool=Path(perf).name,
                           scope='Diagnostic CPU placement for the original one-client JDBC case', cpu_placement=cpu)
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
        for placement in ('same', 'separate'):
            campaign.report['runs'].append(profile(campaign, 'postgres', None, placement, cpu, perf))
            campaign.save()
    campaign.data_files = [ROOT / f'build/size-scaling/ramsql_load_t_{size}.sql' for size in SIZES]
    campaign.check_engine('ramsql')
    campaign.save()
    check_xml(campaign.out / 'ramsql-checks.xml', required)
    check_xml(campaign.out / 'ramsql-capabilities.xml', CAPABILITY_CHECKS)
    with campaign.running('ramsql') as child:
        for path in campaign.data_files:
            campaign.psql(path)
        checks = campaign.out / 'fresh-profile-checks.xml'
        if campaign.command('fresh-profile-checks', campaign.java('check', 'ramsql', ['--junit', str(checks)])):
            raise RuntimeError('Original size-scaling correctness checks failed')
        check_xml(checks, required)
        for placement in ('separate', 'same'):
            campaign.report['runs'].append(profile(campaign, 'ramsql', child, placement, cpu, perf))
            campaign.save()
    campaign.report['completed'] = True
    campaign.save()


if __name__ == '__main__':
    main(sys.argv[1])
