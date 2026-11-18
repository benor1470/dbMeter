"""Same-VM RAMSQL control/candidate diagnostic; never all-engine qualification."""
import json
import os
import re
import statistics
import sys
from xml.etree.ElementTree import ParseError

from bootstrap import ROOT, RUNTIME, VERSIONS, digest, paired_cache_budgets
from evidence import CAPABILITY_CHECKS, MIXED, check_xml, publish, read_run
from live_benchmark import Campaign, ENGINES, SIZES, check_original_inputs
from runner import host_snapshot

ORDER = (('baseline', 'candidate'), ('candidate', 'baseline'), ('baseline', 'candidate'))
SUITE = VERSIONS.get('paired_suite', 'sales-subset')
FOCUS = VERSIONS.get('paired_focus', 's3_top_products')
LOCALITY = VERSIONS.get('paired_linux_worker_locality', False)


def paired_cases(focus, suite='sales-subset'):
    if suite == 'size-scaling':
        if focus != 'prepared-pk':
            raise ValueError('Size-scaling pairing requires the original prepared-PK focus')
        return tuple(f's{size}_pk/{clients}' for size in (100000, 1000000) for clients in (1, 8))
    if suite not in MIXED or focus not in MIXED[suite]:
        raise ValueError('Paired focus must name an existing scenario in its original sales suite')
    return ('mixed/1', 'mixed/8', focus + '/1', focus + '/8')


ORIGINAL_CASES = paired_cases(FOCUS, SUITE)
SHARD = os.environ.get('PAIRED_CASE', '')
if SHARD and (not VERSIONS.get('paired_pk_shards') or SHARD not in ORIGINAL_CASES):
    raise ValueError('Invalid original PK shard')
CASES = (SHARD,) if SHARD else ORIGINAL_CASES
REQUIRED_CHECKS = ({f's{size}_{kind}' for size in SIZES for kind in ('a_pk', 'b_filter', 'c_topn', 'd_last')}
                   if SUITE == 'size-scaling' else {'a_totals'} | MIXED[SUITE])


def validate_size_setup(folder, campaign):
    check_original_inputs(campaign.get('original_input_sha256'))
    if campaign.get('checks') != dict.fromkeys(ENGINES, True) or campaign.get('ramsql_capability_probes_passed') is not True:
        raise ValueError('Original size-scaling setup checks did not pass')
    for engine in ENGINES:
        extra = CAPABILITY_CHECKS if engine == 'postgres' else set()
        check_xml(folder / f'{engine}-checks.xml', REQUIRED_CHECKS | extra)
    check_xml(folder / 'ramsql-capabilities.xml', CAPABILITY_CHECKS)


def validate_size_pair_arguments(arguments):
    if '--workers' not in arguments or arguments.index('--workers') == len(arguments) - 1:
        raise ValueError('Missing prepared PK worker capacity argument')
    if int(arguments[arguments.index('--workers') + 1]) < 2:
        raise ValueError('Prepared PK comparison requires the original multi-reactor capacity profile')


def validate_sources(proofs):
    for mode, key in (('baseline', 'ramsql_baseline'), ('candidate', 'ramsql')):
        if proofs[mode]['source'] != VERSIONS[key]:
            raise ValueError('Unpinned paired source: ' + mode)
    for key in ('toolchain', 'build_arguments'):
        if not proofs['baseline'].get(key) or proofs['baseline'][key] != proofs['candidate'].get(key):
            raise ValueError('Paired toolchain/build arguments differ: ' + key)
    manifests = {mode: proofs[mode].get('dependency_manifest_sha256', '') for mode in ('baseline', 'candidate')}
    pins = VERSIONS.get('paired_dependency_manifests')
    if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) for value in manifests.values()):
        raise ValueError('Missing paired dependency manifest digest')
    if pins is not None:
        if manifests != pins:
            raise ValueError('Paired dependency manifests differ from the explicit pins')
    elif manifests['baseline'] != manifests['candidate']:
        raise ValueError('Paired dependency manifests differ')


def validate_locality_pairs(campaign, arguments):
    proofs = campaign['paired_sources']
    if proofs['baseline']['binary_sha256'] != proofs['candidate']['binary_sha256']:
        raise ValueError('Worker locality experiment needs the same binary')
    control = arguments['baseline/1']
    for key, actual in arguments.items():
        candidate = key.startswith('candidate/')
        expected = [*control, '--linux-worker-locality'] if candidate else control
        if '--linux-worker-locality' in control or actual != expected:
            raise ValueError('Worker locality experiment changed arguments beyond the single worker-locality flag')
        facts = campaign['paired_admission'][key]
        if facts.get('startup_fits') is not True:
            raise ValueError('Expected worker placement was not admitted')
        workers = int(actual[actual.index('--workers') + 1])
        affinity = facts.get('live_worker_affinity', {})
        allowed = affinity.get('process', [])
        masks = affinity.get('workers', [])
        if not allowed or len(masks) != workers:
            raise ValueError('Missing live worker affinity proof')
        expected = all(len(mask) == 1 and mask[0] in allowed for mask in masks)
        if candidate and (not expected or len({mask[0] for mask in masks}) != workers):
            raise ValueError('Candidate does not use distinct allowed worker CPUs')
        if not candidate and any(mask != allowed for mask in masks):
            raise ValueError('Control worker unexpectedly uses restricted affinity')


def summarize_pairs(folder, campaign):
    validate_sources(campaign['paired_sources'])
    cache = paired_cache_budgets(VERSIONS)
    if cache and (campaign['paired_read_cache_bytes'] != cache or
                  campaign['paired_sources']['baseline']['binary_sha256'] != campaign['paired_sources']['candidate']['binary_sha256']):
        raise ValueError('Cache experiment must use the same pinned executable')
    if campaign.get('diagnostic_only') is not True or campaign.get('merge_allowed') is not False:
        raise ValueError('Paired measurements must remain diagnostic')
    if campaign['suite'] != SUITE or campaign['expected_cases'] != list(CASES):
        raise ValueError('Paired matrix changed')
    if tuple(tuple(order) for order in campaign.get('paired_order', [])) != ORDER:
        raise ValueError('Paired order changed')
    if SUITE == 'size-scaling':
        validate_size_setup(folder, campaign)
        arguments = campaign['paired_server_arguments']
        if set(arguments) != {f'{mode}/{pair}' for pair in (1, 2, 3) for mode in ('baseline', 'candidate')}:
            raise ValueError('Missing paired server capacity arguments')
        control = arguments['baseline/1']
        validate_size_pair_arguments(control)
        if VERSIONS.get("paired_linux_worker_locality", False):
            validate_locality_pairs(campaign, arguments)
        elif any(actual != control for actual in arguments.values()):
            raise ValueError('Paired server capacity arguments differ')
    scheduled = [(pair, mode, case) for pair, order in enumerate(ORDER, 1) for mode in order for case in CASES]
    if [(run['trial'], run['variant'], run['case']) for run in campaign['runs']] != scheduled:
        raise ValueError('Paired trial schedule is incomplete or reordered')
    expected = {(pair, mode, case) for pair in (1, 2, 3) for mode in ('baseline', 'candidate') for case in CASES}
    values = {}
    for run in campaign['runs']:
        key = (run['trial'], run['variant'], run['case'])
        if key not in expected or key in values:
            raise ValueError('Unexpected/duplicate paired trial')
        if run['binary_sha256'] != campaign['paired_sources'][run['variant']]['binary_sha256']:
            raise ValueError('Paired binary substitution')
        if cache and run.get('read_cache_bytes') != cache[run['variant']]:
            raise ValueError('Paired read cache budget differs from its pin')
        values[key] = read_run(folder, campaign, run)
    if values.keys() != expected:
        raise ValueError('Missing paired trial; partial runs are not scored')
    for pair in (1, 2, 3):
        for mode in ('baseline', 'candidate'):
            check_xml(folder / f'{mode}-pair{pair}-checks.xml', REQUIRED_CHECKS)
            if SUITE == 'size-scaling':
                check_xml(folder / f'{mode}-pair{pair}-capabilities.xml', CAPABILITY_CHECKS)
    rows = []
    for case in CASES:
        row = dict(case=case, engines={}, paired_qps_ratios=[])
        for mode in ('baseline', 'candidate'):
            trials = [values[pair, mode, case] for pair in (1, 2, 3)]
            row['engines'][mode] = dict(median_qps=statistics.median(t['qps'] for t in trials),
                min_qps=min(t['qps'] for t in trials), max_qps=max(t['qps'] for t in trials),
                median_p99_us=statistics.median(t['p99_us'] for t in trials), trials=trials)
        row['paired_qps_ratios'] = [values[pair, 'candidate', case]['qps'] / values[pair, 'baseline', case]['qps'] for pair in (1, 2, 3)]
        rows.append(row)
    return dict(diagnostic_only=True, merge_allowed=False, rows=rows)


class PairedCampaign(Campaign):
    def __init__(self):
        super().__init__(SUITE, 'qualification')
        self.candidate_binary = self.binary
        self.cache_budgets = paired_cache_budgets(VERSIONS)
        if self.cache_budgets is None and not LOCALITY:
            self.baseline_binary = RUNTIME / 'downloaded-baseline/ramsql/bin/ramsql'
            self.baseline_binary.chmod(0o755)
            baseline = json.loads((RUNTIME / 'downloaded-baseline/ramsql-provenance.json').read_text())
        else:
            self.baseline_binary = self.candidate_binary
            baseline = self.ram_provenance
        if digest(self.baseline_binary) != baseline['binary_sha256']:
            raise ValueError('Downloaded baseline artifact changed')
        self.report.update(diagnostic_only=True, merge_allowed=False,
            scope='Paired RAMSQL control/candidate; not reference-engine or full-suite qualification',
            expected_cases=list(CASES), paired_order=ORDER, paired_focus=FOCUS,
            original_worker_locality_experiment=LOCALITY,
            paired_sources=dict(baseline=baseline, candidate=self.ram_provenance), paired_admission={},
            paired_server_arguments={},
            paired_read_cache_bytes=self.cache_budgets, paired_read_cache={})
        validate_sources(self.report['paired_sources'])
        self.save()

    def run(self):
        generator = 'gen_expected.py' if SUITE == 'sales-full' else 'gen.py'
        if self.command('original-generator', [sys.executable, '-I', str(self.folder / generator)]):
            raise RuntimeError('Original generator failed')
        self.setup()
        if not all(self.report['checks'].get(engine) for engine in ('ramsql', 'postgres', 'irdb')):
            self.report['gaps'].append('Paired timing requires all original checks to pass first')
            self.save()
            return
        if SUITE == 'size-scaling':
            validate_size_setup(self.out, self.report)
        for pair, order in enumerate(ORDER, 1):
            for mode in order:
                self.binary = self.candidate_binary if mode == 'candidate' else self.baseline_binary
                self.ramsql_source = self.report['paired_sources'][mode]['source']
                self.ramsql_read_cache_bytes = (self.cache_budgets[mode] if self.cache_budgets else
                                               self.ramsql_source.get('read_cache_bytes', 0))
                self.ramsql_worker_locality = LOCALITY and mode == 'candidate'
                with self.running('ramsql'):
                    self.report['paired_admission'][f'{mode}/{pair}'] = self.report['ramsql_machine_capacity']
                    self.report['paired_server_arguments'][f'{mode}/{pair}'] = arguments = self.report['ramsql_server_arguments']
                    if SUITE == 'size-scaling':
                        validate_size_pair_arguments(arguments)
                        expected = self.report['paired_server_arguments']['baseline/1']
                        if self.ramsql_worker_locality:
                            expected = [*expected, '--linux-worker-locality']
                        if arguments != expected:
                            raise RuntimeError('Paired server capacity arguments differ')
                    self.report['paired_read_cache'][f'{mode}/{pair}'] = self.report['ramsql_read_cache']
                    for source in self.data_files:
                        self.psql(source)
                    name = f'{mode}-pair{pair}-checks'
                    checks = self.out / (name + '.xml')
                    if self.command(name, self.java('check', 'ramsql', ['--junit', str(checks)])):
                        raise RuntimeError('Original full-suite checks failed: ' + mode)
                    check_xml(checks, REQUIRED_CHECKS)
                    for case in CASES:
                        scenario, clients = case.split('/')
                        name = f'{mode}-pair{pair}-{scenario}-{clients}'
                        target = self.out / name
                        tail = ['--set', 'load.threads=' + clients, '--set', 'load.warmup=15s',
                                '--set', 'load.duration=60s', '--set', 'load.rampUp=5s',
                                '--node', 'local', '--run-id', name, '--out', str(target)]
                        if scenario != 'mixed':
                            tail += ['--only', scenario]
                        before = host_snapshot()
                        code = self.command(name, self.java('load', 'ramsql', tail))
                        raw = target / 'local.json'
                        self.report['runs'].append(dict(engine='ramsql', variant=mode, trial=pair, case=case,
                            binary_sha256=self.report['paired_sources'][mode]['binary_sha256'], returncode=code,
                            read_cache_bytes=self.ramsql_read_cache_bytes,
                            report=str(raw.relative_to(self.out)) if raw.exists() else None,
                            host_before=before, host_after=host_snapshot()))
                        self.save()
                        read_run(self.out, self.report, self.report['runs'][-1])
                    # Capability probes create extra tables, so keep them off
                    # the timed schema and check them after the timed reads.
                    if SUITE == 'size-scaling':
                        capability_name = f'{mode}-pair{pair}-capabilities'
                        if not self.check_size_capabilities(capability_name):
                            raise RuntimeError('Original capacity checks failed: ' + mode)
                        check_xml(self.out / (capability_name + '.xml'), CAPABILITY_CHECKS)
        self.report['completed'] = True
        self.save()

    def publish(self):
        try:
            result = summarize_pairs(self.out, self.report)
        except (ValueError, KeyError, OSError, TypeError, ParseError) as error:
            result = dict(diagnostic_only=True, merge_allowed=False, rows=[], error=str(error))
        (self.out / 'paired-summary.json').write_text(json.dumps(result, indent=2) + '\n')
        lines = ['# Same-runner RAMSQL A/B diagnostic', '',
                 '**Not eligible for the all-engine advantage gate or a general performance claim.**', '',
                 self.report['ci']['run_url'], '',
                 '| Original case / clients | Baseline QPS median | Candidate QPS median | Paired candidate/baseline ratios |',
                 '| --- | ---: | ---: | --- |']
        if self.cache_budgets:
            lines[4:4] = ['The same executable ran with caching disabled and with the pinned cache budget. Both variants use the original warmup phase.', '']
        for row in result['rows']:
            lines.append('| ' + ' | '.join([row['case'], f"{row['engines']['baseline']['median_qps']:.2f}",
                f"{row['engines']['candidate']['median_qps']:.2f}", ', '.join(f'{r:.3f}' for r in row['paired_qps_ratios'])]) + ' |')
        lines += ['', 'AB / BA / AB, same runner and toolchain, pinned dependency manifests, fresh RAMSQL load and original full checks per variant/pair, fresh JVM per case, original 5s ramp / 15s warmup / 60s measure. Every trial, p99, source/binary checksum and host pressure snapshot is retained. No post-hoc subset is substituted.', '']
        lines += ['- ' + gap for gap in self.report['gaps']]
        for error in (self.report.get('error'), result.get('error')):
            if error:
                lines += ['- ' + error]
        text = '\n'.join(lines) + '\n'
        (self.out / 'paired-summary.md').write_text(text)
        publish(text)


def main():
    campaign = PairedCampaign()
    try:
        campaign.run()
    except BaseException as error:
        campaign.report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        campaign.save()
        campaign.publish()


if __name__ == '__main__':
    main()
