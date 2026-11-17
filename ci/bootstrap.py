"""Checksum-verified benchmark dependencies; no benchmark engine code lives here."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / '.ci-runtime'
VERSIONS = json.loads((ROOT / 'ci/versions.json').read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(spec, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with urllib.request.urlopen(spec['url'], timeout=120) as response:
            target.write_bytes(response.read())
    if digest(target) != spec['sha256']:
        raise ValueError(f'Checksum mismatch for {target.name}')
    return target


def workflow_matrices(versions):
    candidate = versions['ramsql']
    baseline = versions.get('ramsql_baseline')
    paired = 'release' not in candidate and baseline is not None and baseline != candidate
    builds = {'variant': ['ramsql', 'ramsql-baseline'] if paired else ['ramsql']}
    jobs = [dict(suite=suite, kind='live') for suite in ('size-scaling', 'sales-subset', 'sales-full')]
    jobs.extend(dict(suite=suite, kind='diagnostic') for suite in ('sales-subset', 'sales-full'))
    if paired:
        jobs.append(dict(suite='sales-subset', kind='paired'))
    return builds, {'include': jobs}


def source_capacity_flags(spec):
    """Translate a pinned build profile to RAMSQL options."""
    profile = spec.get('capacity_profile')
    if profile is None:
        return []
    bounds = {'tables': (1, 65535), 'columns': (1, 255), 'text-bytes': (1, 65535)}
    if not isinstance(profile, dict) or not profile or not profile.keys() <= bounds.keys():
        raise ValueError('Invalid RAMSQL source capacity profile')
    flags = []
    for name, value in sorted(profile.items()):
        low, high = bounds[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'Invalid RAMSQL source capacity: {name}')
        flags.append(f'-D{name}={value}')
    return flags


def install_release(spec, runtime):
    """Install only the qualified Linux binary from the pinned release archive."""
    release = spec['release']
    revision, tag, version = spec['revision'], release['tag'], release['version']
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Published RAMSQL revision must be a full commit SHA')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', tag) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', version):
        raise ValueError('Invalid release tag or version')
    for key in ('manifest_sha256', 'archive_sha256', 'image_sha256'):
        if not re.fullmatch(r'[a-f0-9]{64}', release[key]):
            raise ValueError(f'Invalid published RAMSQL {key}')
    target = 'x86_64-linux'
    prefix = f'ramsql-{version}-{target}'
    filename = prefix + '.tar.gz'
    base = f'https://github.com/SB0LTD/ramsql/releases/download/{tag}/'
    manifest_path = download(
        dict(url=base + 'release-manifest.json', sha256=release['manifest_sha256']),
        runtime / 'published' / tag / 'release-manifest.json',
    )
    manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    if (manifest['schema'], manifest['project'], manifest['revision'], manifest['version']) != (1, 'ramsql', revision, version):
        raise ValueError('Published RAMSQL manifest identity differs from its pin')
    records = [record for record in manifest['targets'] if record['target'] == target]
    if len(records) != 1:
        raise ValueError('Published RAMSQL Linux target must appear exactly once')
    record = records[0]
    archives = [asset for asset in record['artifacts'] if asset['file'] == filename]
    proof = record['hosted_runtime_evidence']
    if (record['revision'] != revision or record['version'] != version or len(archives) != 1 or
            archives[0]['sha256'] != release['archive_sha256'] or proof['passed'] is not True or
            proof['image_sha256'] != release['image_sha256'] or
            (proof['target'], proof['revision'], proof['version'], proof['archive'], proof['archive_sha256']) !=
            (target, revision, version, filename, release['archive_sha256'])):
        raise ValueError('Published RAMSQL archive is missing matching hosted runtime evidence')
    archive = download(dict(url=base + filename, sha256=release['archive_sha256']), manifest_path.parent / filename)
    if archive.stat().st_size != archives[0]['bytes']:
        raise ValueError('Published RAMSQL archive size differs from its manifest')
    with tarfile.open(archive, mode='r:gz') as contents:
        members = [member for member in contents.getmembers() if member.name == f'{prefix}/ramsql']
        if len(members) != 1 or not members[0].isreg():
            raise ValueError('Published RAMSQL archive must have one regular binary')
        with contents.extractfile(members[0]) as stream:
            image = stream.read()
    image_sha256 = hashlib.sha256(image).hexdigest()
    if not image.startswith(b'\x7fELF') or image_sha256 != proof['image_sha256']:
        raise ValueError('Published RAMSQL binary differs from the tested image')
    binary = runtime / 'ramsql/bin/ramsql'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(image)
    binary.chmod(0o755)
    provenance = dict(source=spec, binary_sha256=image_sha256, published_runtime=proof,
                      toolchain=record['provenance']['toolchain'], capabilities=record['provenance']['capabilities'])
    (runtime / 'ramsql-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf8')


def main():
    if sys.argv[1] == 'workflow-matrices':
        build, benchmark = workflow_matrices(VERSIONS)
        print('build=' + json.dumps(build, separators=(',', ':')))
        print('benchmark=' + json.dumps(benchmark, separators=(',', ':')))
        return
    RUNTIME.mkdir(exist_ok=True)
    if sys.argv[1] in ('ramsql', 'ramsql-baseline'):
        runtime = RUNTIME if sys.argv[1] == 'ramsql' else RUNTIME / 'baseline-build'
        runtime.mkdir(exist_ok=True)
        spec = VERSIONS['ramsql' if sys.argv[1] == 'ramsql' else 'ramsql_baseline']
        if 'release' in spec:
            install_release(spec, runtime)
            return
        source = download(spec, runtime / 'ramsql-source.tar.gz')
        sdk = download(VERSIONS['sig'], runtime / 'sig.tar.xz')
        for archive, name in [(source, 'source'), (sdk, 'sdk')]:
            folder = runtime / name
            folder.mkdir(exist_ok=False)
            with tarfile.open(archive) as contents:
                contents.extractall(folder, filter='data')
        compiler = next((runtime / 'sdk').rglob('bin/sig'))
        library = compiler.parent.parent / 'lib'
        checkout = next((runtime / 'source').iterdir())
        # Use the product's exact published no-libc runtime preparation, not
        # the older SDK runtime that requires libc and disables native workers.
        prepared = runtime / 'prepared-runtime'
        subprocess.run([sys.executable, str(checkout / 'tools/prepare_sig_runtime.py'),
                        '--installed-lib', str(library), '--output', str(prepared)], cwd=checkout, check=True)
        library = prepared / 'lib'
        env = dict(os.environ, SIG_LIB_DIR=str(library))
        build_arguments = ['build', '-Doptimize=ReleaseFast', '-Dtarget=x86_64-linux-musl', '-Dlink-libc=false'] + source_capacity_flags(spec)
        subprocess.run([str(compiler), *build_arguments, '--prefix', str(runtime / 'ramsql')], cwd=checkout, env=env, check=True)
        binary = runtime / 'ramsql/bin/ramsql'
        capabilities = json.loads(subprocess.check_output([str(binary), '--capabilities']))
        for name, expected in spec.get('capacity_profile', {}).items():
            if capabilities[name.replace('-', '_')] != expected:
                raise ValueError(f'RAMSQL executable capacity differs from its pin: {name}')
        (runtime / 'ramsql-provenance.json').write_text(json.dumps(dict(
            source=spec, toolchain=VERSIONS['sig'], binary_sha256=digest(binary),
            dependency_manifest_sha256=digest(checkout / 'build.sig.zon'),
            runtime_preparation=json.loads((prepared / 'runtime-preparation.json').read_text()),
            capabilities=capabilities,
            build_arguments=build_arguments), indent=2) + '\n')
    elif sys.argv[1] == 'dependencies':
        drivers = ROOT / 'drivers'
        drivers.mkdir(exist_ok=True)
        encoded = ''.join(os.environ.get(f'IRDB_JDBC_PART_{index}', '') for index in range(1, 6))
        if not encoded:
            raise ValueError('The supplied IRDB driver must be configured in the five private repository secrets; no synthetic IRDB results are allowed')
        jar = drivers / 'jirdb-2025-02-02.jar'
        jar.write_bytes(base64.b64decode(encoded, validate=True))
        if digest(jar) != VERSIONS['irdb_jdbc_sha256']:
            raise ValueError('Private IRDB client checksum mismatch')
        download(VERSIONS['postgres_jdbc'], drivers / 'postgresql-42.7.4.jar')
        download(VERSIONS['fastutil'], drivers / 'fastutil-8.5.15.jar')
        archive = download(VERSIONS['irdb'], RUNTIME / 'irdb.zip')
        with zipfile.ZipFile(archive) as contents:
            for member in contents.infolist():
                target = (RUNTIME / 'irdb' / member.filename).resolve()
                if not target.is_relative_to((RUNTIME / 'irdb').resolve()):
                    raise ValueError('Unsafe server archive path')
            contents.extractall(RUNTIME / 'irdb')
        subprocess.run(['docker', 'pull', VERSIONS['postgres_image']], check=True)
        image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', VERSIONS['postgres_image']]))[0]
        (RUNTIME / 'dependencies.json').write_text(json.dumps(dict(
            versions=VERSIONS, postgres_image_id=image['Id'], postgres_image_digests=image['RepoDigests'],
            java=subprocess.check_output(['java', '-version'], stderr=subprocess.STDOUT, text=True),
            dotnet=subprocess.check_output(['dotnet', '--info'], text=True)), indent=2) + '\n')
    else:
        raise ValueError('Expected ramsql or dependencies')


if __name__ == '__main__':
    main()
