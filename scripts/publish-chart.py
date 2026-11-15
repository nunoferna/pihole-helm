#!/usr/bin/env python3
"""Resume publication of ONE tested, signed archive to GitHub, Pages and OCI.

Preview is the default. --execute is reserved for the manually invoked release
workflow. Existing package bytes must match; destinations are checked separately.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

import yaml


def run(command, check=True, input=None):
    result = subprocess.run(command, input=input, capture_output=True, text=True, timeout=240)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-2000:])
    return result


def api(path, body=None, missing=False):
    command = ['gh', 'api', path]
    if body is not None:
        command += ['--method', 'POST', '--input', '-']
    result = run(command, check=False, input=json.dumps(body) if body is not None else None)
    if result.returncode:
        if missing and 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError(result.stderr[-2000:])
    return json.loads(result.stdout) if result.stdout.strip() else None


def index_has_package(index, manifest, url):
    if not isinstance(index, dict) or not isinstance(index.get('entries', {}), dict):
        raise ValueError('Malformed Pages index')
    versions = index.get('entries', {}).get(manifest['name'], [])
    if not isinstance(versions, list) or any(not isinstance(entry, dict) for entry in versions):
        raise ValueError('Malformed Pages version entries')
    entries = [e for e in versions if e.get('version') == manifest['version']]
    if not entries:
        return False
    if len(entries) != 1 or entries[0].get('digest') != manifest['sha256'] or entries[0].get('urls') != [url]:
        raise ValueError('Published Pages index conflicts with the tested package')
    return True


def publish_github(args, manifest, package, tag):
    ref = api(f'repos/{args.repo}/git/ref/tags/{tag}', missing=True)
    if ref:
        target = ref['object']
        for _ in range(5):
            if target['type'] != 'tag':
                break
            target = api(f'repos/{args.repo}/git/tags/{target["sha"]}')['object']
        if target['type'] != 'commit' or target['sha'] != manifest['sourceSha']:
            raise ValueError('Existing release tag points to another source commit')
    else:
        api(f'repos/{args.repo}/git/refs', {'ref': 'refs/tags/' + tag, 'sha': manifest['sourceSha']})
    release = api(f'repos/{args.repo}/releases/tags/{tag}', missing=True)
    prerelease = '-' in manifest['version'].split('+', 1)[0]
    if release is None:
        command = ['gh', 'release', 'create', tag, '--repo', args.repo, '--verify-tag', '--generate-notes', '--title', tag]
        command += ['--prerelease', '--latest=false'] if prerelease else ['--latest']
        run(command)
        release = api(f'repos/{args.repo}/releases/tags/{tag}')
    elif release['prerelease'] != prerelease or release['draft']:
        raise ValueError('Existing release type differs or is still a draft; review it explicitly')
    assets = {a['name'] for a in release['assets']}
    files = [package, Path(str(package) + '.prov'), args.directory / 'manifest.json', args.directory / 'SHA256SUMS', args.directory / 'pihole-signing-key.asc']
    # Re-signing changes a signature timestamp. Reuse a valid existing signature
    # only after verifying it against the exact archive and trusted keyring.
    with tempfile.TemporaryDirectory(prefix='pihole-release-assets-') as directory:
        target = Path(directory)
        shutil.copy2(package, target / package.name)
        for file in files:
            if file.name in assets:
                run(['gh', 'release', 'download', tag, '--repo', args.repo, '--pattern', file.name, '--dir', directory, '--clobber'])
                existing = target / file.name
                if file.suffix == '.prov':
                    run(['helm', 'verify', str(target / package.name), '--keyring', str(args.keyring)])
                    shutil.copy2(existing, file)
                elif existing.read_bytes() != file.read_bytes():
                    raise ValueError('Existing GitHub asset differs: ' + file.name)
            else:
                run(['gh', 'release', 'upload', tag, str(file), '--repo', args.repo])


def publish_oci(args, manifest, package):
    base = args.oci_base or 'oci://ghcr.io/' + args.repo.split('/')[0].lower() + '/charts'
    reference = base.rstrip('/') + '/' + manifest['name']
    transport = ['--plain-http'] if getattr(args, 'oci_plain_http', False) else []
    with tempfile.TemporaryDirectory(prefix='pihole-oci-check-') as directory:
        command = ['helm', 'pull', reference, '--version', manifest['version'], '--destination', directory, *transport]
        existing = run(command, check=False)
        if existing.returncode:
            if not re.search(r'\b404\b|manifest unknown|not found', existing.stderr, re.I):
                raise RuntimeError(existing.stderr)
            run(['helm', 'push', str(package), reference.rsplit('/', 1)[0], *transport])
        else:
            if (Path(directory) / package.name).read_bytes() != package.read_bytes():
                raise ValueError('Existing OCI chart bytes differ from the tested archive')
        # Check post-publication bytes AND the Helm provenance layer every time.
        run(command + ['--prov', '--verify', '--keyring', str(args.keyring)])
        if (Path(directory) / package.name).read_bytes() != package.read_bytes():
            raise ValueError('OCI round trip changed package bytes')


def publish_pages(args, manifest, package, tag):
    path = f'repos/{args.repo}/contents/index.yaml?ref=gh-pages'
    existing = api(path, missing=True)
    url = f'https://github.com/{args.repo}/releases/download/{tag}/{package.name}'
    index = yaml.safe_load(base64.b64decode(existing['content'])) if existing else {}
    if index_has_package(index, manifest, url):
        return
    with tempfile.TemporaryDirectory(prefix='pihole-chart-index-') as directory:
        target = Path(directory); shutil.copy2(package, target / package.name)
        command = ['helm', 'repo', 'index', directory, '--url', url.rsplit('/', 1)[0]]
        if existing:
            old = target / 'previous.yaml'; old.write_text(yaml.safe_dump(index)); command += ['--merge', str(old)]
        run(command)
        content = (target / 'index.yaml').read_text()
        if not index_has_package(yaml.safe_load(content), manifest, url):
            raise ValueError('Generated index omitted the verified package')
    if existing:
        # Contents API provides an optimistic lease on the current index blob.
        body = {'message': 'Publish ' + tag, 'branch': 'gh-pages', 'sha': existing['sha'],
                'content': base64.b64encode(content.encode()).decode()}
        run(['gh', 'api', f'repos/{args.repo}/contents/index.yaml', '--method', 'PUT', '--input', '-'], input=json.dumps(body))
    else:
        # Make an index-only root commit; do not copy main onto the Pages branch.
        branch = api(f'repos/{args.repo}/git/ref/heads/gh-pages', missing=True)
        if branch:
            body = {'message': 'Publish ' + tag, 'branch': 'gh-pages', 'content': base64.b64encode(content.encode()).decode()}
            run(['gh', 'api', f'repos/{args.repo}/contents/index.yaml', '--method', 'PUT', '--input', '-'], input=json.dumps(body))
        else:
            tree = api(f'repos/{args.repo}/git/trees', {'tree': [{'path': 'index.yaml', 'mode': '100644', 'type': 'blob', 'content': content}]})
            commit = api(f'repos/{args.repo}/git/commits', {'message': 'Publish ' + tag, 'tree': tree['sha'], 'parents': []})
            api(f'repos/{args.repo}/git/refs', {'ref': 'refs/heads/gh-pages', 'sha': commit['sha']})


def verify_pages_delivery(args, manifest, package, tag, configuration):
    # GITHUB_TOKEN commits do not automatically trigger a Pages build. Request
    # one explicitly, including when a previous attempt already wrote the index.
    api(f'repos/{args.repo}/pages/builds', {})
    expected = f'https://github.com/{args.repo}/releases/download/{tag}/{package.name}'
    base = configuration['html_url'].rstrip('/') + '/index.yaml'
    deadline = time.monotonic() + 300
    last_error = 'index has not appeared'
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(base + '?release=' + manifest['sourceSha'] + '&check=' + str(int(time.time())),
                                             headers={'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(request, timeout=10) as response:
                index = yaml.safe_load(response.read())
            if index_has_package(index, manifest, expected):
                return
        except (OSError, ValueError, yaml.YAMLError) as error:
            last_error = str(error)
        time.sleep(5)
    raise RuntimeError('Pages did not serve the verified index before the deadline: ' + last_error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--keyring', type=Path, required=True)
    parser.add_argument('--oci-base', help='Optional registry base for isolated publication testing')
    parser.add_argument('--oci-plain-http', action='store_true', help='Local registry fixtures only')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.oci_plain_http and not re.match(r'^oci://(localhost|127\.0\.0\.1):[0-9]+/', args.oci_base or ''):
        parser.error('Plain HTTP registry tests require an explicit loopback OCI base')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
        parser.error('Expected owner/repository')
    spec = importlib.util.spec_from_file_location('package_chart', Path(__file__).with_name('package-chart.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    manifest, package = module.verify_manifest(args.directory)
    if manifest['sourceDirty'] or manifest['sourceSha'] != args.source_sha:
        raise ValueError('Publication requires a clean package from the exact successful test SHA')
    for name in (package.name + '.prov', 'manifest.json', 'SHA256SUMS', 'pihole-signing-key.asc'):
        if not (args.directory / name).is_file():
            raise ValueError('Missing release file: ' + name)
    run(['helm', 'verify', str(package), '--keyring', str(args.keyring)])
    tag = manifest['name'] + '-' + manifest['version']
    if not args.execute:
        print(json.dumps({'preview': True, 'tag': tag, 'sha256': manifest['sha256'], 'destinations': ['GitHub release', 'GHCR OCI', 'Pages Helm index']}))
        return
    configuration = api(f'repos/{args.repo}/pages')
    if (configuration.get('source', {}).get('branch') != 'gh-pages' or
        configuration.get('source', {}).get('path', '/') != '/' or
        configuration.get('build_type', 'legacy') != 'legacy' or
        not configuration.get('html_url', '').startswith('https://')):
        raise ValueError('Configure HTTPS Pages from the gh-pages branch root before publication')
    publish_github(args, manifest, package, tag)
    publish_oci(args, manifest, package)
    publish_pages(args, manifest, package, tag)
    verify_pages_delivery(args, manifest, package, tag, configuration)
    print(json.dumps({'published': tag, 'sha256': manifest['sha256'], 'destinationsVerified': ['GitHub release', 'GHCR OCI', 'Pages Helm index']}))


if __name__ == '__main__':
    main()
