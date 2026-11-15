#!/usr/bin/env python3
"""Build a reproducible Helm archive, or sign the SAME previously tested archive.

Signing uses Helm's two-document OpenPGP provenance format without repackaging.
Use an isolated GNUPGHOME in CI. No publication is performed by this script.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def run(command, input=None):
    result = subprocess.run(command, input=input, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])
    return result.stdout


def canonical_package(chart):
    with tempfile.TemporaryDirectory(prefix='pihole-helm-package-') as directory:
        run(['helm', 'package', str(chart), '--destination', directory])
        source = next(Path(directory).glob('*.tgz'))
        output = io.BytesIO()
        with tarfile.open(source, 'r:gz') as archive:
            with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as target:
                    for member in sorted(archive.getmembers(), key=lambda m: m.name):
                        if not member.isfile():
                            raise ValueError('Only regular chart files are supported')
                        data = archive.extractfile(member).read()
                        header = tarfile.TarInfo(member.name)
                        header.size, header.mode = len(data), 0o644
                        header.mtime, header.uid, header.gid = 0, 0, 0
                        target.addfile(header, io.BytesIO(data))
        return output.getvalue()


def verify_manifest(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    package = directory / manifest['package']
    if package.name != manifest['package'] or hashlib.sha256(package.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Package does not match the tested manifest')
    if package.name != manifest['name'] + '-' + manifest['version'] + '.tgz':
        raise ValueError('Package filename differs from manifest name/version')
    metadata = yaml.safe_load(run(['helm', 'show', 'chart', str(package)]))
    if any(metadata.get(key) != manifest[key] for key in ('name', 'version', 'appVersion')):
        raise ValueError('Packaged chart metadata differs from the tested manifest')
    return manifest, package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chart', type=Path, default=ROOT / 'charts/pihole')
    parser.add_argument('--require-clean', action='store_true')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--expected-source')
    parser.add_argument('--sign-key', help='Sign the existing archive; never build it again')
    parser.add_argument('--passphrase-file', type=Path)
    parser.add_argument('--keyring', type=Path, help='Public keyring to verify newly generated provenance with Helm')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.verify_only:
        manifest, package = verify_manifest(args.output)
        if args.expected_source and manifest['sourceSha'] != args.expected_source:
            raise ValueError('Tested archive belongs to another source commit')
    elif args.sign_key:
        manifest, package = verify_manifest(args.output)
        if manifest['sourceDirty']:
            raise ValueError('Refusing release signing of a dirty source checkout')
        metadata = run(['helm', 'show', 'chart', str(package)]).decode()
        body = metadata.rstrip() + '\n...\n' + yaml.safe_dump({'files': {package.name: 'sha256:' + manifest['sha256']}}, sort_keys=False)
        command = ['gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', '--local-user', args.sign_key,
                   '--digest-algo', 'SHA512', '--output', str(package) + '.prov', '--clearsign']
        if args.passphrase_file:
            command[1:1] = ['--passphrase-file', str(args.passphrase_file)]
        run(command, body.encode())
        if not args.keyring:
            raise ValueError('--keyring is required for signature verification')
        run(['helm', 'verify', str(package), '--keyring', str(args.keyring)])
    else:
        source_sha = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip()
        dirty = bool(run(['git', '-C', str(ROOT), 'status', '--porcelain']).strip())
        if args.require_clean and dirty:
            raise ValueError('Source checkout is dirty')
        metadata = yaml.safe_load((args.chart / 'Chart.yaml').read_text())
        data = canonical_package(args.chart)
        filename = metadata['name'] + '-' + metadata['version'] + '.tgz'
        package = args.output / filename
        if package.exists() and package.read_bytes() != data:
            raise ValueError('Existing package differs; use a new output directory')
        package.write_bytes(data)
        manifest = {'formatVersion': 1, 'sourceSha': source_sha, 'sourceDirty': dirty,
                    'name': metadata['name'], 'version': metadata['version'], 'appVersion': metadata['appVersion'],
                    'package': filename, 'sha256': hashlib.sha256(data).hexdigest(),
                    'compatibility': json.loads((args.chart / 'files/compatibility.json').read_text())}
        (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (args.output / 'SHA256SUMS').write_text(manifest['sha256'] + '  ' + filename + '\n')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
