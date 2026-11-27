"""Run every original alpha-era JDBC check on RAMSQL and native PostgreSQL.

The old aggregate-gap expectation is explicitly replaced with COUNT(*) = 500;
the original SQL, data, CSVs, rollback checks and Java implementation are intact.
This is a correctness gate, never a throughput measurement.
"""
import json
from pathlib import Path
import subprocess
import uuid

from bootstrap import ROOT, RUNTIME, VERSIONS, digest, download
from evidence import check_xml
from live_benchmark import free_port, original_inputs, wait_port


CHECKS = {'a_row_count', 'q_pk', 'q_scan', 'tx_rollback',
          'tx_rollback_gone', 'z_gap_aggregates'}


def main():
    output = ROOT / 'reports/legacy'
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema=1, correctness_only=True, passed=False,
                  original_input_sha256=original_inputs(),
                  candidate=VERSIONS['ramsql'],
                  expectation_override={
                      'check.z_gap_aggregates.expect.error': '',
                      'check.z_gap_aggregates.expect.value': '500'},
                  override_reason='COUNT is supported; the alpha.2 unsupported-feature expectation is obsolete',
                  checks=sorted(CHECKS), engines={})

    def command(label, argv, *, check=True):
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=120)
        (output / (label + '.stdout')).write_text(result.stdout, encoding='utf-8')
        (output / (label + '.stderr')).write_text(result.stderr, encoding='utf-8')
        if check and result.returncode:
            raise RuntimeError(f'{label} failed ({result.returncode}); see retained stderr')
        return result

    container = 'dbmeter-legacy-' + uuid.uuid4().hex
    process = None
    try:
        proof = json.loads((RUNTIME / 'ramsql-provenance.json').read_text())
        binary = RUNTIME / 'ramsql/bin/ramsql'
        if proof['source'] != VERSIONS['ramsql'] or digest(binary) != proof['binary_sha256']:
            raise ValueError('Legacy check binary differs from the pinned candidate')
        report['binary_sha256'] = digest(binary)
        download(VERSIONS['postgres_jdbc'], ROOT / 'drivers/postgresql-42.7.4.jar')
        command('build-original-java', ['sh', 'build.sh'])
        command('pull-native-postgres', ['docker', 'pull', VERSIONS['postgres_image']])
        for engine in ('ramsql', 'postgres'):
            port = free_port()
            if engine == 'ramsql':
                argv = [str(binary), '--serve', '--memory', '--insecure-local', '--port', str(port),
                        '--workers', '1', '--max-clients', '2', '--idle-ms', '300000',
                        '--rows', '512', '--versions', '1024', '--result-rows', '512', '--work-rows', '1024']
                with (output / 'ramsql-server.stdout').open('w') as stdout, \
                        (output / 'ramsql-server.stderr').open('w') as stderr:
                    process = subprocess.Popen(argv, cwd=ROOT, stdout=stdout, stderr=stderr)
                wait_port(port, process)
                database = 'ramsql'
            else:
                argv = ['docker', 'run', '--rm', '--detach', '--name', container, '--network=host',
                        '--env', 'POSTGRES_HOST_AUTH_METHOD=trust', VERSIONS['postgres_image'],
                        'postgres', '-c', 'listen_addresses=127.0.0.1', '-c', 'port=' + str(port)]
                command('start-native-postgres', argv)
                wait_port(port)
                database = 'postgres'
            report['engines'][engine] = dict(server_arguments=argv, passed=False)
            check = ['java', '-Xms32m', '-Xmx256m', '-jar', str(ROOT / 'build/dbmeter.jar'), 'check',
                     '-c', str(ROOT / 'examples/ramsql/ramsql.properties'),
                     '--set', f'jdbc.url=jdbc:postgresql://127.0.0.1:{port}/{database}',
                     '--set', 'run.databaseLabel=' + engine,
                     '--junit', str(output / (engine + '.xml'))]
            for key, value in report['expectation_override'].items():
                check += ['--set', key + '=' + value]
            report['engines'][engine]['check_arguments'] = check
            command(engine + '-checks', check)
            check_xml(output / (engine + '.xml'), CHECKS)
            report['engines'][engine]['passed'] = True
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                process = None
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for label, argv in (
                ('native-postgres-logs', ['docker', 'logs', container]),
                ('stop-owned-native-postgres', ['docker', 'stop', container])):
            try:
                command(label, argv, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                report.setdefault('cleanup_errors', []).append(str(error))
        (output / 'legacy-correctness.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
