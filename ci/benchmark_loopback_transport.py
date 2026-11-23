"""Paired diagnostics with the original prepared JDBC workload and one RAMSQL binary."""
import json
from pathlib import Path
import statistics
import sys

from bootstrap import ROOT, VERSIONS, digest
from evidence import CAPABILITY_CHECKS, check_xml, read_run
from live_benchmark import Campaign, SIZES, original_inputs
from loopback_transport import attachment, isolated_cgroup, membership, verify
from runner import host_snapshot

ORDER = (('hash', 'indexed'), ('indexed', 'hash'), ('hash', 'indexed'))
CHECKS = {f's{size}_{check}' for size in SIZES for check in ('a_pk', 'b_filter', 'c_topn', 'd_last')}
BINARY = 'ff25e9fab66c987a08edfdf8e4530c44b462a464c2126f188c0946cb9647733e'


def cpu(cgroup):
    values = dict(line.split() for line in (cgroup / 'cpu.stat').read_text().splitlines())
    return {name: int(values[name]) for name in ('usage_usec', 'user_usec', 'system_usec')}


def check(campaign, name, cases):
    xml = campaign.out / (name + '.xml')
    cmd = campaign.java('check', 'ramsql', ['--junit', str(xml)])
    if cases == CAPABILITY_CHECKS:
        cmd[cmd.index('--only') + 1] = ','.join(sorted(cases))
    if campaign.command(name, cmd):
        raise RuntimeError(f'Original SQL correctness failed in {name}')
    check_xml(xml, cases)


def main(probes, clients):
    if clients not in (1, 8):
        raise ValueError('Expected an original one- or eight-client case')
    campaign = Campaign('size-scaling', 'qualification', 1000000)
    port = campaign.port['ramsql']
    case = f's1000000_pk/{clients}'
    if campaign.ram_provenance['source'] != VERSIONS['ramsql'] or digest(campaign.binary) != BINARY:
        raise ValueError('This experiment requires the exact previously qualified RAMSQL executable')
    probe_digests = {name: digest(path) for name, path in probes.items()}
    for name, path in probes.items():
        if probe_digests[name] != path.with_suffix('.sha256').read_text().strip():
            raise ValueError(f'Socket-map probe differs from its build artifact: {name}')
    campaign.report.update(diagnostic_only=True, merge_allowed=False, paired_order=ORDER,
                           expected_cases=[case], scope='Hash versus indexed peers on the same Linux loopback deployment',
                           probe_sha256=probe_digests, original_input_sha256=original_inputs())
    campaign.save()
    if campaign.command('original-generator', [sys.executable, '-I', str(campaign.folder / 'gen.py')]):
        raise RuntimeError('Original size-scaling generator failed')
    campaign.prepare_configs()
    campaign.data_files = [ROOT / f'build/size-scaling/ramsql_load_t_{size}.sql' for size in SIZES]
    with isolated_cgroup() as cgroup:
        campaign.report['wire'] = {}
        for name, probe in probes.items():
            wire = campaign.out / f'wire-{name}'
            wire.mkdir()
            campaign.report['wire'][name] = verify(probe, cgroup, port, ('duplex',), wire)
        campaign.save()
        for trial, order in enumerate(ORDER, 1):
            for variant in order:
                label = f'{variant}-{trial}'
                lease = attachment(probes[variant], cgroup, port, 'duplex', campaign.out / f'{label}-socket-map.log')
                with lease, campaign.running('ramsql') as server:
                    if membership(server.pid) != f'/{cgroup.name}':
                        raise RuntimeError('The RAMSQL listener escaped the isolated cgroup')
                    for source in campaign.data_files:
                        campaign.psql(source)
                    check(campaign, label + '-checks', CHECKS)
                    raw = campaign.out / label / 'local.json'
                    args = ['--only', 's1000000_pk', '--set', f'load.threads={clients}',
                            '--set', 'load.rampUp=5s', '--set', 'load.warmup=15s',
                            '--set', 'load.duration=60s', '--node', 'local', '--run-id', label,
                            '--out', str(raw.parent)]
                    host_before = host_snapshot()
                    before = cpu(cgroup)
                    code = campaign.command(label, campaign.java('load', 'ramsql', args))
                    elapsed = {name: count - before[name] for name, count in cpu(cgroup).items()}
                    run = dict(engine='ramsql', case=case, trial=trial, variant=variant, returncode=code,
                               report=str(raw.relative_to(campaign.out)), cgroup_cpu_including_warmup=elapsed,
                               host_before=host_before, host_after=host_snapshot())
                    campaign.report['runs'].append(run)
                    campaign.save()
                    run['result'] = read_run(campaign.out, campaign.report, run)
                    check(campaign, label + '-capabilities', CAPABILITY_CHECKS)
                    campaign.save()
    results = {(r['trial'], r['variant']): r['result'] for r in campaign.report['runs']}
    ratios = [results[(i, 'indexed')]['qps'] / results[(i, 'hash')]['qps'] for i in range(1, 4)]
    campaign.report['summary'] = dict(paired_qps_ratios=ratios, median_paired_ratio=statistics.median(ratios),
                                      median_qps={v: statistics.median(results[i, v]['qps'] for i in range(1, 4))
                                                  for v in ORDER[0]},
                                      median_p99_us={v: statistics.median(results[i, v]['p99_us'] for i in range(1, 4))
                                                     for v in ORDER[0]})
    campaign.save()
    print(json.dumps(campaign.report['summary'], indent=2))


if __name__ == '__main__':
    main({name: Path(path).resolve(strict=True) for name, path in zip(ORDER[0], sys.argv[1:3], strict=True)},
         int(sys.argv[3]))
