#!/usr/bin/env python3
"""Blocking rendered-manifest and pinned-image security checks; rejects empty scans.

Two explicitly scoped manifest exceptions are documented in docs/security.md.
No image vulnerability exclusions and no global rule suppression are applied.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import tarfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / 'charts/pihole'


def chart_file(relative):
    if CHART.is_dir():
        return (CHART / relative).read_text()
    with tarfile.open(CHART, 'r:gz') as archive:
        return archive.extractfile('pihole/' + relative).read().decode()


def accepted_manifest_finding(finding, objects):
    if finding['ID'] == 'KSV014':
        workload = next(o for o in objects if o['kind'] == 'StatefulSet')
        name = workload['metadata']['name']
        message = f"Container 'pihole' of StatefulSet '{name}' should set 'securityContext.readOnlyRootFilesystem' to true"
        return finding['Message'] == message
    if finding['ID'] == 'AVD-KSV-0109':
        for artifact in (o for o in objects if o['kind'] == 'ConfigMap' and 'import-lists.sh' in o.get('data', {})):
            data = artifact['data']
            # Accept the scanner's source-code/password-variable false positive
            # only for the exact reviewed script and this exact policy artifact.
            if (set(data) == {'policy.json', 'policy.jq', 'import-lists.sh'} and
                data['import-lists.sh'] == chart_file('files/import-lists.sh') and
                data['policy.jq'] == chart_file('files/policy.jq')):
                expected = "ConfigMap '" + artifact['metadata']['name'] + "' in '" + artifact['metadata'].get('namespace', 'default')
                expected += "' namespace stores secrets in key(s) or value(s) '{\"    response=$(jq -nc --rawfile password /etc/pihole/cli_pw '{password\"}'"
                return finding['Message'] == expected
    return False


def main():
    global CHART
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trivy', default='trivy')
    parser.add_argument('--chart', type=Path, default=CHART)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--platform', action='append', choices=['linux/amd64', 'linux/arm64'])
    args = parser.parse_args()
    CHART = args.chart
    args.reports.mkdir(parents=True, exist_ok=True)
    def run(command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        return result.stdout
    report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'checks': [], 'acceptedManifestExceptions': []}
    if CHART.is_file():
        report['tested_archive_sha256'] = hashlib.sha256(CHART.read_bytes()).hexdigest()
    try:
        report['trivyVersion'] = run([args.trivy, '--version']).splitlines()[0]
        with tempfile.TemporaryDirectory(prefix='pihole-rendered-security-') as directory:
            manifest = Path(directory) / 'pihole.yaml'
            manifest.write_text(run(['helm', 'template', 'security', str(CHART), '--namespace', 'security',
                '--set', 'pihole.web.existingSecret=scan-fixture', '--set', 'replicas=3', '--set', 'metrics.enabled=true',
                '--set', 'networkPolicy.enabled=true', '--set', 'monitoring.syntheticDns.enabled=true']))
            objects = [o for o in yaml.safe_load_all(manifest.read_text()) if o]
            output = args.reports / 'manifests.json'
            run([args.trivy, 'config', directory, '--severity', 'HIGH,CRITICAL', '--format', 'json', '--output', str(output), '--timeout', '3m'])
            scan = json.loads(output.read_text())
            if not scan.get('Results') or not any(r.get('Type') == 'kubernetes' for r in scan['Results']):
                raise ValueError('Manifest scanner inspected no Kubernetes resources')
            for result in scan['Results']:
                for finding in result.get('Misconfigurations', []):
                    if finding.get('Status') != 'FAIL':
                        continue
                    if not accepted_manifest_finding(finding, objects):
                        raise ValueError('Unaccepted manifest finding: ' + finding['ID'] + ': ' + finding['Message'])
                    report['acceptedManifestExceptions'].append({'id': finding['ID'], 'message': finding['Message']})
            report['checks'].append({'check': 'rendered_manifests', 'passed': True, 'resources': len(objects)})
        baseline = json.loads(chart_file('files/compatibility.json'))
        images = {'pihole': 'pihole/pihole:' + baseline['dockerTag'] + '@' + baseline['digest'],
                  'exporter': 'ekofr/pihole-exporter:' + baseline['exporterTag'] + '@' + baseline['exporterDigest']}
        for platform in args.platform or ['linux/amd64', 'linux/arm64']:
            for name, image in images.items():
                output = args.reports / (name + '-' + platform.replace('/', '-') + '.json')
                run([args.trivy, 'image', '--image-src', 'remote', '--platform', platform, '--scanners', 'vuln',
                     '--severity', 'HIGH,CRITICAL', '--format', 'json', '--output', str(output), '--timeout', '3m', image])
                scan = json.loads(output.read_text())
                if not scan.get('Results') or scan.get('Metadata', {}).get('ImageConfig', {}).get('architecture') != platform.split('/')[1]:
                    raise ValueError('Image scan is empty or architecture differs: ' + name + ' ' + platform)
                findings = [v for r in scan['Results'] for v in r.get('Vulnerabilities', [])]
                if findings:
                    raise ValueError('Release-blocking image vulnerabilities: ' + name + ' ' + platform + ': ' + ', '.join(v['VulnerabilityID'] for v in findings))
                report['checks'].append({'check': 'image_vulnerabilities', 'image': image, 'platform': platform, 'passed': True})
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        report['error'] = str(error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    if report.get('error'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
