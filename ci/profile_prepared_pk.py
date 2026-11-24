"""Profile two exact RAMSQL reactor revisions with stock JDBC on one runner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from bootstrap import ROOT, RUNTIME, VERSIONS, digest, download
from evidence import check_xml, read_run
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


def profile(campaign, child, perf, variant, trial):
    name = f'{variant}-{trial}-s1000000_pk-8'
    destination = campaign.out / name
    argv = campaign.java('load', 'ramsql', ['--only', 's1000000_pk', '--set', 'load.threads=8',
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
                threads_before = thread_counters(child.pid)
                jdbc_before = thread_counters(load.pid)
                sample_start_ns = time.time_ns()
                subprocess.run(['sudo', perf, 'record', '--quiet', '--event', 'cpu-clock', '--freq', '199',
                    '--call-graph', 'dwarf,4096', '--pid', f'{child.pid},{load.pid}', '--output', str(trace),
                    '--', 'sleep', '30'], stdout=perf_log, stderr=subprocess.STDOUT, check=True, timeout=45)
                sample_end_ns = time.time_ns()
                threads_after = thread_counters(child.pid)
                jdbc_after = thread_counters(load.pid)
                if load.poll() is not None:
                    raise RuntimeError('JDBC load ended before the sample interval completed')
                returncode = load.wait(timeout=90)
            finally:
                if load.poll() is None:
                    load.kill()
    run = dict(engine='ramsql', variant=variant, trial=trial, case='s1000000_pk/8', returncode=returncode,
               report=str((destination / 'local.json').relative_to(campaign.out)),
               host_before=before, host_after=host_snapshot())
    run['result'] = read_run(campaign.out, campaign.report, run)
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


def main(perf):
    driver = download(VERSIONS['postgres_jdbc'], ROOT / 'drivers/postgresql-42.7.4.jar')
    (RUNTIME / 'dependencies.json').write_text(json.dumps({'postgres_jdbc_sha256': digest(driver)}))
    campaign = Campaign('size-scaling', 'qualification', 1000000)
    baseline = RUNTIME / 'baseline-build'
    binaries = {'candidate': campaign.binary, 'baseline': baseline / 'ramsql/bin/ramsql'}
    provenance = {'candidate': campaign.ram_provenance,
                  'baseline': json.loads((baseline / 'ramsql-provenance.json').read_text())}
    for label, spec in (('candidate', 'ramsql'), ('baseline', 'ramsql_baseline')):
        expected = VERSIONS[spec]
        record = provenance[label]
        if (record['source'] != expected or record['binary_sha256'] != digest(binaries[label]) or
                record['dependency_manifest_sha256'] != expected['dependency_manifest_sha256']):
            raise ValueError(f'{label} artifact differs from its exact pin')
    for field in ('build_arguments', 'runtime_preparation', 'dependency_manifest_sha256'):
        if provenance['candidate'][field] != provenance['baseline'][field]:
            raise ValueError(f'Unpaired RAMSQL build setting: {field}')
    campaign.report.update(diagnostic_only=True, merge_allowed=False, variants=provenance,
                           scope='Paired plaintext TCP profile of idle-reactor servicing')
    campaign.save()
    if campaign.command('original-generator', [sys.executable, '-I', str(campaign.folder / 'gen.py')]):
        raise RuntimeError('Original size-scaling generator failed')
    campaign.prepare_configs()
    campaign.report['original_input_sha256'] = original_inputs()
    campaign.data_files = [ROOT / f'build/size-scaling/ramsql_load_t_{size}.sql' for size in SIZES]
    required = {f's{size}_{kind}' for size in SIZES for kind in ('a_pk', 'b_filter', 'c_topn', 'd_last')}
    workers = min(campaign.worker_limit, len(os.sched_getaffinity(0)))
    if workers < 2:
        raise RuntimeError('Paired profiling requires parallel reactors')
    for trial, label in enumerate(('baseline', 'candidate', 'candidate', 'baseline'), 1):
        campaign.binary = binaries[label]
        with campaign.running('ramsql') as child:
            if campaign.report['ramsql_machine_capacity']['read_workers'] != workers:
                raise RuntimeError('RAMSQL did not admit the shared worker budget')
            for path in campaign.data_files:
                campaign.psql(path)
            name = f'{label}-{trial}-checks'
            checks = campaign.out / (name + '.xml')
            if campaign.command(name, campaign.java('check', 'ramsql', ['--junit', str(checks)])):
                raise RuntimeError(f'Original size-scaling correctness checks failed for {label}')
            check_xml(checks, required)
            campaign.report['runs'].append(profile(campaign, child, perf, label, trial))
            campaign.save()
    campaign.report['completed'] = True
    campaign.save()


if __name__ == '__main__':
    main(sys.argv[1])
