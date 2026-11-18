"""Run the two original million-row filter cases on all three engines."""
import json
import statistics

from evidence import MAX_P99_RATIO, MIN_RATIO, check_original_inputs, read_run, validate_ci
from live_benchmark import Campaign, ENGINES


def main():
    candidate = Campaign('size-scaling', 'qualification', 1_000_000)
    candidate.cases = (('s1000000_filter', 1), ('s1000000_filter', 8))
    expected_cases = [f'{scenario}/{clients}' for scenario, clients in candidate.cases]
    candidate.report.update(diagnostic_only=True, merge_allowed=False, expected_cases=expected_cases)
    candidate.save()
    try:
        candidate.run()
        report = candidate.report
        validate_ci(report['ci'], report['input_revision'])
        check_original_inputs(report['original_input_sha256'])
        assert report['completed'] and report['checks'] == dict.fromkeys(ENGINES, True)
        assert report.get('ramsql_capability_probes_passed') and not report['gaps']
        expected_trials = {(engine, case, number) for engine in ENGINES for case in expected_cases for number in range(1, 4)}
        observed = {(run['engine'], run['case'], run['trial']) for run in report['runs']}
        assert observed == expected_trials and len(report['runs']) == len(expected_trials)
        comparisons = []
        for case in expected_cases:
            results = {}
            for engine in ENGINES:
                runs = [read_run(candidate.out, report, trial) for trial in report['runs'] if trial['case'] == case and trial['engine'] == engine]
                rates = [run['qps'] for run in runs]
                results[engine] = dict(median_qps=statistics.median(rates), min_qps=min(rates), max_qps=max(rates),
                                       median_p99_us=statistics.median(run['p99_us'] for run in runs))
            ram = results['ramsql']
            references = [results[engine] for engine in ENGINES if engine != 'ramsql']
            ratio = ram['median_qps'] / max(row['median_qps'] for row in references)
            passes = (ratio >= MIN_RATIO and ram['min_qps'] > max(row['max_qps'] for row in references)
                      and all(ram['median_p99_us'] <= MAX_P99_RATIO * row['median_p99_us'] for row in references))
            comparisons.append(dict(case=case, engines=results, ratio_to_fastest=ratio, case_passes=passes))
        outcome = dict(diagnostic_only=True, merge_allowed=False, comparisons=comparisons)
        (candidate.out / 'packed-filter-decision.json').write_text(json.dumps(outcome, indent=2) + '\n')
        print(json.dumps(outcome, indent=2))
    except Exception as error:
        candidate.report['error'] = f'{type(error).__name__}: {error}'
        candidate.save()
        raise


if __name__ == '__main__':
    main()
