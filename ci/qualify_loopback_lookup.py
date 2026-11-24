"""Run the original three-engine gate with an explicit, same-host Linux deployment."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sys

from bootstrap import ROOT, VERSIONS, digest
from live_benchmark import Campaign
from loopback_transport import attachment, isolated_cgroup, membership, verify

class LoopbackCampaign(Campaign):
    def irdb_settings(self):
        return 'nocache=false\nLogAll=false\n'

    def setup(self):
        super().setup()
        if not self.report['checks'].get('irdb'):
            return
        source = 'live_size_scaling'
        indexed = source + '_clustered'
        classpath = os.pathsep.join(str(ROOT / f'drivers/{name}') for name in
                                    ('jirdb-2025-02-02.jar', 'fastutil-8.5.15.jar'))
        command = ['java', '-cp', classpath, ROOT / 'ci/IrdbClusteredReference.java',
                   str(self.port['irdb']), source, indexed]
        with self.running('irdb'):
            if self.command('irdb-clustered-index-setup', command):
                raise RuntimeError('Vendor clustered index setup failed')
        config = self.configs['irdb']
        original = config.read_text()
        url = f'jdbc:irdb://127.0.0.1:{self.port["irdb"]}/'
        if original.count(url + source) != 1:
            raise RuntimeError('Expected the original IRDB JDBC URL')
        config.write_text(original.replace(url + source, url + indexed))
        self.report['irdb_reference'] = dict(clustered_index='t_1000000 (id)', database=indexed,
                                             settings=self.irdb_settings().splitlines(),
                                             setup_log='irdb-clustered-index-setup.log')
        # Verify the complete original correctness suite with the saved index
        # after a server restart, as in each of the timed trials.
        original_checks = self.out / 'irdb-checks.xml'
        original_checks.rename(self.out / 'irdb-unindexed-checks.xml')
        self.check_engine('irdb')
        self.save()

    @contextmanager
    def running(self, engine):
        if engine != 'ramsql':
            with super().running(engine) as server:
                yield server
            return
        log = self.out / 'ramsql-loopback.log'
        with attachment(self.probe, self.cgroup, self.port[engine], 'duplex', log), super().running(engine) as server:
            if membership(server.pid) != f'/{self.cgroup.name}':
                raise RuntimeError('RAMSQL listener escaped the isolated cgroup')
            yield server


def main(probe, clients):
    if clients not in (1, 8):
        raise ValueError('Expected an original lookup client count')
    campaign = LoopbackCampaign('size-scaling', 'qualification', 1000000)
    spec = VERSIONS['ramsql']
    if (campaign.ram_provenance['source'] != spec or
            campaign.ram_provenance['dependency_manifest_sha256'] != spec['dependency_manifest_sha256']):
        raise ValueError('RAMSQL binary provenance does not match the pinned qualification build')
    probe_sha256 = probe.with_suffix('.sha256').read_text().strip()
    if digest(probe) != probe_sha256:
        raise ValueError('Socket-map probe differs from the portable build artifact')
    probe_revision = probe.with_suffix('.revision').read_text().strip()
    if re.fullmatch(r'[0-9a-f]{40}', probe_revision) is None:
        raise ValueError('The socket-map artifact must record its exact ZPM source')
    campaign.cases = [('s1000000_pk', clients)]
    campaign.probe = probe
    campaign.report.update(lookup_clients=clients, expected_cases=[f's1000000_pk/{clients}'],
                           deployment='RAMSQL Linux same-host indexed TCP socket map, manually enabled',
                           full_suite_merge_allowed=False,
                           probe_source=probe_revision,
                           probe_sha256=probe_sha256)
    campaign.save()
    try:
        with isolated_cgroup() as campaign.cgroup:
            campaign.report['loopback_wire_proof'] = verify(probe, campaign.cgroup, campaign.port['ramsql'],
                                                           ('duplex',), campaign.out)
            campaign.save()
            campaign.run()
    except BaseException as error:
        campaign.report['error'] = f'{type(error).__name__}: {error}'
        campaign.save()
        raise


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve(strict=True), int(sys.argv[2]))
