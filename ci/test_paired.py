"""Synthetic A/B evidence fixtures only; these are never benchmark results."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from bootstrap import VERSIONS
from evidence import CAPABILITY_CHECKS, MIXED
from live_benchmark import ENGINES, PROFILES
from paired_benchmark import CASES, ORDER, REQUIRED_CHECKS, SUITE, summarize_pairs, paired_cases


class PairedContracts(unittest.TestCase):
    def xml(self, name, checks):
        node = ET.Element('testsuite')
        for check in sorted(checks):
            ET.SubElement(node, 'testcase', name=check)
        ET.ElementTree(node).write(self.folder / (name + '.xml'))

    def setUp(self):
        self.enterContext(patch.dict(VERSIONS))
        VERSIONS.pop('paired_dependency_manifests', None)
        VERSIONS.pop('paired_read_cache_bytes', None)
        VERSIONS.pop('paired_equal_read_cache_bytes', None)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        proofs = {mode: dict(source=copy.deepcopy(VERSIONS[key]), binary_sha256=char * 64,
            dependency_manifest_sha256='e' * 64, toolchain=VERSIONS['sig'], build_arguments=['build', '-Doptimize=ReleaseFast'])
            for mode, key, char in (('baseline', 'ramsql_baseline', 'a'), ('candidate', 'ramsql', 'b'))}
        self.campaign = dict(suite=SUITE, profile='qualification', policy=PROFILES['qualification'],
            diagnostic_only=True, merge_allowed=False, expected_cases=list(CASES), paired_order=ORDER, paired_sources=proofs, runs=[])
        if SUITE == 'size-scaling':
            self.enterContext(patch('paired_benchmark.check_original_inputs'))
            self.campaign.update(checks=dict.fromkeys(ENGINES, True), ramsql_capability_probes_passed=True,
                paired_server_arguments={f'{mode}/{pair}': ['--workers', '8', '--work-rows', '4096']
                                         for pair in (1, 2, 3) for mode in ('baseline', 'candidate')})
            for engine in ENGINES:
                extra = CAPABILITY_CHECKS if engine == 'postgres' else set()
                self.xml(f'{engine}-checks', REQUIRED_CHECKS | extra)
            self.xml('ramsql-capabilities', CAPABILITY_CHECKS)
        for pair, order in enumerate(ORDER, 1):
            for mode in order:
                self.xml(f'{mode}-pair{pair}-checks', REQUIRED_CHECKS)
                if SUITE == 'size-scaling':
                    self.xml(f'{mode}-pair{pair}-capabilities', CAPABILITY_CHECKS)
                for case in CASES:
                    scenario, clients = case.split('/')
                    count = 200 if mode == 'candidate' else 100
                    names = MIXED[SUITE] if scenario == 'mixed' else {scenario}
                    raw = dict(dbmeterReport=1, measuredStart=100000, measuredEnd=160000,
                        database=dict(label='ramsql'), nodes=[dict(settings=dict(threads=int(clients), duration='PT1M', warmup='PT15S', rampUp='PT5S'))],
                        scenarios={name: dict(count=count, errors=0, latencyMicros=dict(subBits=5, count=count, min=10, max=10, buckets={'10': count})) for name in names})
                    path = f'{mode}-{pair}-{scenario}-{clients}.json'
                    (self.folder / path).write_text(json.dumps(raw))
                    self.campaign['runs'].append(dict(engine='ramsql', variant=mode, trial=pair, case=case,
                        binary_sha256=proofs[mode]['binary_sha256'], returncode=0, report=path))

    def test_complete_three_pairs(self):
        result = summarize_pairs(self.folder, self.campaign)
        self.assertFalse(result['merge_allowed'])
        self.assertTrue(result['diagnostic_only'])
        self.assertEqual(len(result['rows']), len(CASES))
        for row in result['rows']:
            self.assertEqual(row['paired_qps_ratios'], [2, 2, 2])

    def test_focus_keeps_mix_and_original_scenario_only(self):
        for focus in MIXED['sales-subset']:
            self.assertEqual(paired_cases(focus), ('mixed/1', 'mixed/8', focus + '/1', focus + '/8'))
        for focus in ('mixed', 'new_faster_query', ''):
            with self.assertRaises(ValueError):
                paired_cases(focus)
        case = 'q1_region_month'
        self.assertEqual(paired_cases(case, 'sales-full'), ('mixed/1', 'mixed/8', case + '/1', case + '/8'))
        with self.assertRaises(ValueError):
            paired_cases(case, 'sales-subset')

    def test_size_scaling_focus_selects_original_cases(self):
        self.assertEqual(paired_cases('prepared-pk', 'size-scaling'),
                         ('s100000_pk/1', 's100000_pk/8', 's1000000_pk/1', 's1000000_pk/8'))
        self.assertEqual(paired_cases('s1000000_filter', 'size-scaling'),
                         ('s1000000_filter/1', 's1000000_filter/8'))
        self.assertEqual(paired_cases('s1000000_topn', 'size-scaling'), ('s1000000_topn/1',))
        with self.assertRaisesRegex(ValueError, 'original size-scaling paired focus'):
            paired_cases('s100000_topn', 'size-scaling')

    @unittest.skipUnless(SUITE == 'size-scaling', 'prepared PK evidence')
    def test_size_setup_and_pair_capabilities_are_required(self):
        self.xml('postgres-checks', REQUIRED_CHECKS)
        with self.assertRaisesRegex(ValueError, 'dropped or duplicated'):
            summarize_pairs(self.folder, self.campaign)
        self.xml('postgres-checks', REQUIRED_CHECKS | CAPABILITY_CHECKS)
        self.xml('candidate-pair3-capabilities', set())
        with self.assertRaisesRegex(ValueError, 'dropped or duplicated'):
            summarize_pairs(self.folder, self.campaign)

    @unittest.skipUnless(SUITE == 'size-scaling', 'prepared PK evidence')
    def test_size_pair_requires_identical_multi_worker_arguments(self):
        self.campaign['paired_server_arguments']['candidate/2'] = ['--workers', '3', '--work-rows', '4096']
        with self.assertRaisesRegex(ValueError, 'arguments differ'):
            summarize_pairs(self.folder, self.campaign)
        for case in self.campaign['paired_server_arguments']:
            self.campaign['paired_server_arguments'][case] = ['--workers', '1']
        with self.assertRaisesRegex(ValueError, 'multi-reactor'):
            summarize_pairs(self.folder, self.campaign)

    @unittest.skipUnless(SUITE == 'size-scaling', 'equal cache evidence')
    def test_equal_cache_requires_a_verified_medium_tier(self):
        budget = 256 * 1024 * 1024
        VERSIONS['paired_equal_read_cache_bytes'] = budget
        self.campaign['paired_admission'] = {}
        for key, args in self.campaign['paired_server_arguments'].items():
            args.extend(['--read-cache-bytes', str(budget)])
            admitted = dict(startup_fits=True, read_cache_bytes=budget, read_cache_region_bytes=budget,
                            read_cache_rows_per_entry=128, read_cache_entries=1)
            if key.startswith('candidate/'):
                admitted.update(read_cache_projected_bytes=1024, read_cache_projected_entries=8,
                                read_cache_projected_rows_per_entry=2048)
            self.campaign['paired_admission'][key] = admitted
        for run in self.campaign['runs']:
            run['read_cache_bytes'] = budget
        self.assertEqual(len(summarize_pairs(self.folder, self.campaign)['rows']), len(CASES))
        self.campaign['paired_admission']['candidate/1']['read_cache_projected_entries'] = 0
        with self.assertRaisesRegex(ValueError, 'tier did not fit'):
            summarize_pairs(self.folder, self.campaign)

    def test_missing_trial_rejected(self):
        self.campaign['runs'].pop()
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_duplicate_or_reordered_trial_rejected(self):
        self.campaign['runs'][1] = self.campaign['runs'][0]
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_sql_error_rejected(self):
        path = self.folder / self.campaign['runs'][0]['report']
        raw = json.loads(path.read_text())
        next(iter(raw['scenarios'].values()))['errors'] = 1
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_binary_substitution_rejected(self):
        self.campaign['runs'][0]['binary_sha256'] = 'c' * 64
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_different_library_rejected(self):
        self.campaign['paired_sources']['baseline']['dependency_manifest_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_explicitly_pinned_library_change(self):
        self.campaign['paired_sources']['baseline']['dependency_manifest_sha256'] = 'f' * 64
        pins = {'baseline': 'f' * 64, 'candidate': 'e' * 64}
        with patch.dict(VERSIONS, paired_dependency_manifests=pins):
            self.assertEqual(len(summarize_pairs(self.folder, self.campaign)['rows']), len(CASES))
            self.campaign['paired_sources']['candidate']['dependency_manifest_sha256'] = 'f' * 64
            with self.assertRaisesRegex(ValueError, 'explicit pins'):
                summarize_pairs(self.folder, self.campaign)

    def test_cache_comparison_requires_exact_binary_and_budget(self):
        budgets = {'baseline': 0, 'candidate': 268435456}
        VERSIONS['ramsql_baseline'] = VERSIONS['ramsql']
        VERSIONS['paired_read_cache_bytes'] = budgets
        self.campaign['paired_read_cache_bytes'] = budgets
        binary = self.campaign['paired_sources']['candidate']
        self.campaign['paired_sources']['baseline'] = copy.deepcopy(binary)
        for run in self.campaign['runs']:
            run['binary_sha256'] = binary['binary_sha256']
            run['read_cache_bytes'] = budgets[run['variant']]
        self.assertEqual(len(summarize_pairs(self.folder, self.campaign)['rows']), len(CASES))
        self.campaign['runs'][0]['read_cache_bytes'] = 1
        with self.assertRaisesRegex(ValueError, 'cache budget'):
            summarize_pairs(self.folder, self.campaign)

    def test_failed_fresh_check_rejected(self):
        path = self.folder / 'baseline-pair1-checks.xml'
        xml = ET.parse(path)
        ET.SubElement(list(xml.getroot())[0], 'failure')
        xml.write(path)
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)


if __name__ == '__main__':
    unittest.main()
