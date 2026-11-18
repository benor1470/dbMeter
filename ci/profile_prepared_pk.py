"""Profile the actual RAMSQL service during an unchanged dbMeter JDBC read."""
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
    for thread in (Path('/proc') / str(pid) / 'task').iterdir():
        stat = (thread / 'stat').read_text().rsplit(')', 1)[1].split()
        cpu, wait, switches = map(int, (thread / 'schedstat').read_text().split())
        result[thread.name] = dict(user_ticks=int(stat[11]), kernel_ticks=int(stat[12]),
                                   cpu_ns=cpu, runnable_wait_ns=wait, switches=switches)
    return result


def profile(campaign, child, client_count, perf):
    name = f's1000000_pk-{client_count}'
    destination = campaign.out / name
    argv = campaign.java('load', 'ramsql', ['--only', 's1000000_pk', '--set', f'load.threads={client_count}',
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
                subprocess.run(['sudo', perf, 'record', '--quiet', '--event', 'cpu-clock', '--freq', '199',
                    '--call-graph', 'dwarf,4096', '--pid', str(child.pid), '--output', str(trace),
                    '--', 'sleep', '30'], stdout=perf_log, stderr=subprocess.STDOUT, check=True, timeout=45)
                threads_after = thread_counters(child.pid)
                if load.poll() is not None:
                    raise RuntimeError('JDBC load ended before the sample interval completed')
                returncode = load.wait(timeout=90)
            finally:
                if load.poll() is None:
                    load.kill()
    run = dict(engine='ramsql', case=f's1000000_pk/{client_count}', returncode=returncode,
               report=str((destination / 'local.json').relative_to(campaign.out)),
               host_before=before, host_after=host_snapshot())
    run['result'] = read_run(campaign.out, campaign.report, run)
    run['thread_deltas'] = {tid: {key: after - threads_before[tid][key] for key, after in counters.items()}
                            for tid, counters in threads_after.items() if tid in threads_before}
    for kind, options in [('self', ['--no-children']), ('inclusive', ['--children'])]:
        with (campaign.out / f'{name}-{kind}.txt').open('wb') as report:
            subprocess.run(['sudo', perf, 'report', '--stdio', '--input', str(trace),
                            '--sort', 'dso,symbol', '--percent-limit', '0.2', *options],
                           stdout=report, check=True, timeout=40)
    return run


def main(perf):
    pg_driver = download(VERSIONS['postgres_jdbc'], ROOT / 'drivers/postgresql-42.7.4.jar')
    (RUNTIME / 'dependencies.json').write_text(json.dumps({'postgres_jdbc_sha256': digest(pg_driver)}))
    campaign = Campaign('size-scaling', 'qualification', 1000000)
    campaign.report.update(diagnostic_only=True, merge_allowed=False, profiling_tool=Path(perf).name,
                           scope='Original prepared JDBC read under RAMSQL CPU sampling')
    campaign.save()
    generator = campaign.folder / 'gen.py'
    if campaign.command('original-generator', [sys.executable, '-I', str(generator)]):
        raise RuntimeError('Original size-scaling generator failed')
    campaign.prepare_configs()
    campaign.report['original_input_sha256'] = original_inputs()
    subprocess.run(['docker', 'pull', VERSIONS['postgres_image']], check=True, timeout=180)
    required = {f's{size}_{kind}' for size in SIZES for kind in ('a_pk', 'b_filter', 'c_topn', 'd_last')}
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
        for client_count in (1, 8):
            campaign.report['runs'].append(profile(campaign, child, client_count, perf))
            campaign.save()
    campaign.report['completed'] = True
    campaign.save()


if __name__ == '__main__':
    main(sys.argv[1])
