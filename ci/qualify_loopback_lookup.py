"""Run the original three-engine gate with an explicit, same-host Linux deployment."""
from contextlib import contextmanager
from pathlib import Path
import sys

from bootstrap import VERSIONS, digest
from live_benchmark import Campaign
from loopback_transport import attachment, isolated_cgroup, membership, verify

RAMSQL_SHA256 = 'ff25e9fab66c987a08edfdf8e4530c44b462a464c2126f188c0946cb9647733e'
PROBE_SHA256 = 'bf51f1bf4dd568b8d5d85f575b583e5b98348fc4995aaf4958d1f6d3a825a98d'


class LoopbackCampaign(Campaign):
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
    if campaign.ram_provenance['source'] != VERSIONS['ramsql'] or digest(campaign.binary) != RAMSQL_SHA256:
        raise ValueError('RAMSQL is not the exact binary from the paired run')
    if digest(probe) != PROBE_SHA256:
        raise ValueError('Socket-map probe is not the exact paired artifact')
    campaign.cases = [('s1000000_pk', clients)]
    campaign.probe = probe
    campaign.report.update(lookup_clients=clients, expected_cases=[f's1000000_pk/{clients}'],
                           deployment='RAMSQL Linux same-host TCP socket map, manually enabled',
                           diagnostic_only=True, merge_allowed=False,
                           probe_sha256=PROBE_SHA256)
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
