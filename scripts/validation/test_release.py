#!/usr/bin/env python3
"""Real package/signature interoperability and security exception regressions."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import io
import unittest
from unittest import mock
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ReleaseContract(unittest.TestCase):
    def test_prerelease_promotion_is_explicit(self):
        spec = importlib.util.spec_from_file_location('bump_version', ROOT / 'scripts/bump-version.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertEqual(module.next_version('2.0.0-alpha.3', 'stable'), '2.0.0')
        self.assertEqual(module.next_version('2.0.0-alpha.3', 'prerelease'), '2.0.0-alpha.4')
        self.assertEqual(module.next_version('2.0.0', 'prerelease'), '2.0.1-alpha.1')
        self.assertEqual(module.next_version('2.0.0', 'patch'), '2.0.1')
        with self.assertRaisesRegex(ValueError, 'Choose stable'):
            module.next_version('2.0.0-alpha.3', 'patch')

    def test_reproducible_package_signed_bytes_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory(prefix='pihole-release-', dir='/tmp') as directory:
            base = Path(directory)
            repo, output = base / 'repo', base / 'out'
            (repo / 'scripts').mkdir(parents=True)
            shutil.copy2(ROOT / 'scripts/package-chart.py', repo / 'scripts/package-chart.py')
            shutil.copytree(ROOT / 'charts/pihole', repo / 'charts/pihole')
            def run(command, check=True, env=None):
                result = subprocess.run(command, cwd=repo, capture_output=True, text=True, env=env)
                if check and result.returncode:
                    raise RuntimeError(result.stderr)
                return result
            run(['git', 'init', '--quiet'])
            run(['git', 'add', '.'])
            run(['git', '-c', 'user.name=Release fixture', '-c', 'user.email=fixture@invalid', 'commit', '--quiet', '-m', 'fixture'])
            command = ['python3', str(repo / 'scripts/package-chart.py'), '--output', str(output), '--require-clean']
            first = json.loads(run(command).stdout)
            second = json.loads(run(command).stdout)
            self.assertEqual(first['sha256'], second['sha256'])
            package = output / first['package']
            data = package.read_bytes()
            home = base / 'gnupg'; home.mkdir(mode=0o700)
            env = dict(os.environ, GNUPGHOME=str(home))
            run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '', '--quick-generate-key',
                 'Helm fixture <fixture@invalid>', 'rsa2048', 'sign', '1d'], env=env)
            exported = subprocess.run(['gpg', '--export', 'fixture@invalid'], env=env, capture_output=True, check=True).stdout
            keyring = base / 'public.gpg'; keyring.write_bytes(exported)
            run(command + ['--sign-key', 'fixture@invalid', '--keyring', str(keyring)], env=env)
            self.assertEqual(package.read_bytes(), data)
            run(['helm', 'verify', str(package), '--keyring', str(keyring)])
            spec = importlib.util.spec_from_file_location('publish_chart', ROOT / 'scripts/publish-chart.py')
            publisher = importlib.util.module_from_spec(spec); spec.loader.exec_module(publisher)
            public = subprocess.run(['gpg', '--armor', '--export', 'fixture@invalid'], env=env, capture_output=True, check=True).stdout
            (output / 'pihole-signing-key.asc').write_bytes(public)
            settings = SimpleNamespace(repo='fixture/fixture', directory=output, keyring=keyring)
            remote, actions = {}, []
            state = {'tag': None, 'release': False}
            real_run = publisher.run
            def fake_api(path, body=None, missing=False):
                if '/git/ref/tags/' in path:
                    return {'object': {'type': 'commit', 'sha': state['tag']}} if state['tag'] else None
                if path.endswith('/git/refs'):
                    state['tag'] = body['sha']; return {}
                if '/releases/tags/' in path:
                    return {'prerelease': '-' in first['version'], 'draft': False, 'assets': [{'name': n} for n in remote]} if state['release'] else None
                raise AssertionError('Unexpected API call: ' + path)
            def fake_run(argv, check=True, input=None):
                if argv[0] == 'helm':
                    return real_run(argv, check=check, input=input)
                self.assertEqual(argv[:2], ['gh', 'release'])
                actions.append(argv)
                if argv[2] == 'create':
                    state['release'] = True
                    self.assertIn('--prerelease' if '-' in first['version'] else '--latest', argv)
                elif argv[2] == 'upload':
                    file = Path(argv[4]); remote[file.name] = file.read_bytes()
                elif argv[2] == 'download':
                    self.assertIn('--clobber', argv)
                    file = argv[argv.index('--pattern') + 1]
                    destination = Path(argv[argv.index('--dir') + 1]) / file
                    destination.write_bytes(remote[file])
                else:
                    raise AssertionError('Unexpected publication command')
                return subprocess.CompletedProcess(argv, 0, '', '')
            tag = first['name'] + '-' + first['version']
            with mock.patch.object(publisher, 'api', fake_api), mock.patch.object(publisher, 'run', fake_run):
                publisher.publish_github(settings, first, package, tag)
                self.assertEqual(len(remote), 5)
                actions.clear()
                publisher.publish_github(settings, first, package, tag)
                self.assertFalse(any(a[2] == 'upload' for a in actions))
                del remote[package.name + '.prov']; actions.clear()
                publisher.publish_github(settings, first, package, tag)
                self.assertEqual([Path(a[4]).name for a in actions if a[2] == 'upload'], [package.name + '.prov'])
                remote[package.name] = b'conflicting bytes'; actions.clear()
                with self.assertRaisesRegex(ValueError, 'Existing GitHub asset differs'):
                    publisher.publish_github(settings, first, package, tag)
                self.assertFalse(any(a[2] == 'upload' for a in actions))
            url = 'https://example.invalid/' + package.name
            index = {'entries': {first['name']: [{'version': first['version'], 'digest': first['sha256'], 'urls': [url]}]}}
            self.assertTrue(publisher.index_has_package(index, first, url))
            index['entries'][first['name']][0]['digest'] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'index conflicts'):
                publisher.index_has_package(index, first, url)
            served_url = f'https://github.com/fixture/fixture/releases/download/{tag}/{package.name}'
            index['entries'][first['name']][0].update(digest=first['sha256'], urls=[served_url])
            with mock.patch.object(publisher, 'api', return_value={}) as build_request, mock.patch.object(publisher.urllib.request, 'urlopen', return_value=io.BytesIO(yaml.safe_dump(index).encode())):
                publisher.verify_pages_delivery(settings, first, package, tag, {'html_url': 'https://pages.invalid/charts'})
                build_request.assert_called_once_with('repos/fixture/fixture/pages/builds', {})
            if os.environ.get('PIHOLE_TEST_OCI_BASE'):
                settings = SimpleNamespace(repo='fixture/fixture', oci_base=os.environ['PIHOLE_TEST_OCI_BASE'] + '/' + base.name, oci_plain_http=True, keyring=keyring)
                publisher.publish_oci(settings, first, package)
                publisher.publish_oci(settings, first, package)
                package.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                with self.assertRaisesRegex(ValueError, 'Existing OCI chart bytes differ'):
                    publisher.publish_oci(settings, first, package)
                package.write_bytes(data)
            package.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            self.assertNotEqual(run(['helm', 'verify', str(package), '--keyring', str(keyring)], check=False).returncode, 0)
            self.assertNotEqual(run(command + ['--sign-key', 'fixture@invalid', '--keyring', str(keyring)], check=False, env=env).returncode, 0)
            run(['gpgconf', '--kill', 'gpg-agent'], env=env)

    def test_security_exceptions_do_not_hide_other_containers_or_secrets(self):
        spec = importlib.util.spec_from_file_location('security_gate', ROOT / 'scripts/validation/security-gate.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        rendered = subprocess.check_output(['helm', 'template', 'security', str(ROOT / 'charts/pihole'), '--namespace', 'security',
                                            '--set', 'pihole.web.existingSecret=scan-fixture', '--set', 'metrics.enabled=true'], text=True)
        objects = [o for o in yaml.safe_load_all(rendered) if o]
        finding = {'ID': 'KSV014', 'Message': "Container 'pihole' of StatefulSet 'security-pihole' should set 'securityContext.readOnlyRootFilesystem' to true"}
        self.assertTrue(module.accepted_manifest_finding(finding, objects))
        finding['Message'] = "Container 'metrics' of Deployment 'security-pihole-metrics' should set 'securityContext.readOnlyRootFilesystem' to true"
        self.assertFalse(module.accepted_manifest_finding(finding, objects))
        artifact = next(o for o in objects if o['kind'] == 'ConfigMap' and 'policy.json' in o.get('data', {}))
        finding = {'ID': 'AVD-KSV-0109', 'Message': "ConfigMap '" + artifact['metadata']['name'] + "' in 'security' namespace stores secrets in key(s) or value(s) '{\"    response=$(jq -nc --rawfile password /etc/pihole/cli_pw '{password\"}'"}
        self.assertTrue(module.accepted_manifest_finding(finding, objects))
        artifact['data']['password'] = 'must-fail'
        self.assertFalse(module.accepted_manifest_finding(finding, objects))


if __name__ == '__main__':
    unittest.main()
