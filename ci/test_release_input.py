"""Contracts for the published RAMSQL artifact input and dataset bank sizes."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from bootstrap import source_capacity_flags

from bootstrap import digest, download, install_release, workflow_matrices
from live_benchmark import SIZES, ramsql_banks


class ReleaseInputTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runtime = Path(directory.name)
        revision = 'a' * 40
        tag, version, target = 'ci-test', '0.3.0-beta.2', 'x86_64-linux'
        self.spec = {'revision': revision, 'release': {'tag': tag, 'version': version}}
        published = self.runtime / 'published' / tag
        published.mkdir(parents=True)
        prefix = f'ramsql-{version}-{target}'
        self.archive = published / (prefix + '.tar.gz')
        self.image = b'\x7fELF synthetic release fixture'
        with tarfile.open(self.archive, 'w:gz') as archive:
            image = tarfile.TarInfo(prefix + '/ramsql')
            image.size = len(self.image)
            archive.addfile(image, io.BytesIO(self.image))
        archive_sha = digest(self.archive)
        image_sha = hashlib.sha256(self.image).hexdigest()
        self.spec['release'].update(archive_sha256=archive_sha, image_sha256=image_sha)
        runtime_proof = dict(target=target, revision=revision, version=version, archive=self.archive.name,
                             archive_sha256=archive_sha, image_sha256=image_sha, passed=True)
        self.manifest_path = published / 'release-manifest.json'
        self.manifest = dict(schema=1, project='ramsql', revision=revision, version=version, targets=[
            dict(target=target, revision=revision, version=version, hosted_runtime_evidence=runtime_proof,
                 artifacts=[dict(file=self.archive.name, bytes=self.archive.stat().st_size, sha256=archive_sha)],
                 provenance=dict(toolchain={}, capabilities={}))
        ])
        self.write_manifest()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding='utf8')
        self.spec['release']['manifest_sha256'] = digest(self.manifest_path)

    def test_installs_the_tested_bytes(self):
        install_release(self.spec, self.runtime)
        self.assertEqual((self.runtime / 'ramsql/bin/ramsql').read_bytes(), self.image)
        proof = json.loads((self.runtime / 'ramsql-provenance.json').read_text(encoding='utf8'))
        self.assertEqual(proof['source'], self.spec)
        self.assertEqual(proof['binary_sha256'], self.spec['release']['image_sha256'])

    def test_rejects_corrupt_download(self):
        with self.archive.open('ab') as archive:
            archive.write(b'additional bytes')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            install_release(self.spec, self.runtime)

    def test_retries_rate_limited_public_dependency_then_checks_digest(self):
        url = 'https://example.com/pinned.jar'
        limited = HTTPError(url, 429, 'Too Many Requests', {'Retry-After': '5'}, None)
        spec = dict(url=url, sha256=hashlib.sha256(self.image).hexdigest())
        with patch('bootstrap.urllib.request.urlopen', side_effect=[limited, io.BytesIO(self.image)]) as open_url, \
             patch('bootstrap.time.sleep') as sleep:
            path = download(spec, self.runtime / 'pinned.jar')
        self.assertEqual(path.read_bytes(), self.image)
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_requires_runtime_pass_on_the_same_revision(self):
        proof = self.manifest['targets'][0]['hosted_runtime_evidence']
        for key, value in (('passed', False), ('revision', 'b' * 40)):
            with self.subTest(key=key):
                previous = proof[key]
                proof[key] = value
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, 'runtime evidence'):
                    install_release(self.spec, self.runtime)
                proof[key] = previous

    def test_rejects_image_that_differs_from_runtime_test(self):
        wrong_image = 'b' * 64
        self.spec['release']['image_sha256'] = wrong_image
        self.manifest['targets'][0]['hosted_runtime_evidence']['image_sha256'] = wrong_image
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'differs from the tested image'):
            install_release(self.spec, self.runtime)


class MatrixAndBankTests(unittest.TestCase):
    def test_release_mode_keeps_every_reference_engine_campaign(self):
        source_build, source_jobs = workflow_matrices({'ramsql': {'revision': 'candidate'},
                                                       'ramsql_baseline': {'revision': 'baseline'}})
        release_build, release_jobs = workflow_matrices({'ramsql': {'release': {}}})
        self.assertEqual(source_build['variant'], ['ramsql', 'ramsql-baseline'])
        self.assertEqual(release_build['variant'], ['ramsql'])
        self.assertEqual(release_jobs['include'],
                         [job for job in source_jobs['include'] if job['kind'] != 'paired'])
        self.assertEqual({job['suite'] for job in release_jobs['include'] if job['kind'] == 'live'},
                         {'size-scaling', 'sales-subset', 'sales-full'})

    def test_identical_source_profile_does_not_repeat_paired_campaign(self):
        source = {'revision': 'same', 'capacity_profile': {'tables': 32}}
        builds, jobs = workflow_matrices({'ramsql': source, 'ramsql_baseline': dict(source)})
        self.assertEqual(builds['variant'], ['ramsql'])
        self.assertNotIn('paired', {job['kind'] for job in jobs['include']})
        self.assertEqual({job['suite'] for job in jobs['include'] if job['kind'] == 'live'},
                         {'size-scaling', 'sales-subset', 'sales-full'})

    def test_capacity_profile_has_only_typed_build_options(self):
        self.assertEqual(source_capacity_flags({}), [])
        self.assertEqual(
            source_capacity_flags({'capacity_profile': {'tables': 32, 'columns': 9, 'text-bytes': 200}}),
            ['-Dcolumns=9', '-Dtables=32', '-Dtext-bytes=200'],
        )
        for profile in ({'columns': 256}, {'tables': True}, {'text-bytes': '200'}, {'target': 'host'}):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                source_capacity_flags({'capacity_profile': profile})

    def test_larger_catalog_keeps_original_data_banks(self):
        rows, versions = ramsql_banks('size-scaling', 32)
        self.assertEqual(rows, list(SIZES) + [1] * 26)
        self.assertEqual(versions, [size * 2 for size in SIZES] + [2] * 26)
        full_rows, full_versions = ramsql_banks('sales-full', 32)
        self.assertEqual(full_rows[:6], [5, 200, 20, 1000, 50000, 5000000])
        self.assertEqual(full_versions[5:], [5000000 + 65536] + [2] * 26)
        with self.assertRaisesRegex(ValueError, 'fewer compiled table slots'):
            ramsql_banks('sales-subset', 5)


if __name__ == '__main__':
    unittest.main()
