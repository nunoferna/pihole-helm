#!/usr/bin/env python3
"""Compare a DNS pod's denied HTTP egress with an unselected same-node control.

Uses an explicit context and existing DNS pod; creates/deletes only one unique
non-root control pod. Does not change policy. Use after installing reviewed rules.
"""
import argparse
import datetime
import ipaddress
import json
from pathlib import Path
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--pod', required=True)
    parser.add_argument('--destination', default='1.1.1.1', type=ipaddress.IPv4Address)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    def kube(*parts, input=None, check=True):
        result = subprocess.run(['kubectl', '--context', args.context, '-n', args.namespace, *parts],
                                input=input, text=True, capture_output=True, timeout=150)
        if check and result.returncode:
            raise RuntimeError(result.stderr[-1500:])
        return result
    pod = json.loads(kube('get', 'pod', args.pod, '-o', 'json').stdout)
    if pod['metadata'].get('labels', {}).get('app.kubernetes.io/component') != 'dns':
        parser.error('Selected pod is not a chart DNS component')
    image = next(c['image'] for c in pod['spec']['containers'] if c['name'] == 'pihole')
    name = 'pihole-egress-control-' + uuid.uuid4().hex[:10]
    curl = ['curl', '--silent', '--show-error', '--connect-timeout', '3', '--max-time', '5',
            '--output', '/dev/null', '--write-out', '%{http_code}', 'http://' + str(args.destination) + '/']
    control = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name}, 'spec': {
        'nodeName': pod['spec']['nodeName'], 'restartPolicy': 'Never', 'automountServiceAccountToken': False,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{'name': 'control', 'image': image, 'command': curl,
                        'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                        'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '96Mi'}}}]}}
    report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'context': args.context,
              'namespace': args.namespace, 'sourcePod': args.pod, 'sameNodeControl': pod['spec']['nodeName'], 'destination': str(args.destination)}
    created = False
    try:
        kube('create', '-f', '-', input=json.dumps(control)); created = True
        deadline = time.monotonic() + 90
        while True:
            status = json.loads(kube('get', 'pod', name, '-o', 'json').stdout)['status']
            if status['phase'] in ('Succeeded', 'Failed'):
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Control did not complete before deadline')
            time.sleep(1)
        report['controlHTTPStatus'] = kube('logs', name).stdout.strip()
        if status['phase'] != 'Succeeded' or not report['controlHTTPStatus'].isdigit() or report['controlHTTPStatus'] == '000':
            raise RuntimeError('Unselected same-node control could not reach destination; denial would be inconclusive')
        denied = kube('exec', args.pod, '-c', 'pihole', '--', *curl, check=False)
        report['dnsPodExitCode'] = denied.returncode
        report['dnsPodDiagnostic'] = denied.stderr[-500:]
        if denied.returncode not in (7, 28):
            raise RuntimeError('Selected DNS pod did not demonstrate denied HTTP transport')
        report['passed'] = True
    except Exception as error:
        report['passed'] = False; report['error'] = str(error)
    finally:
        if created:
            report['controlRemoved'] = kube('delete', 'pod', name, '--wait=true', '--timeout=60s', check=False).returncode == 0
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    if not report.get('passed') or not report.get('controlRemoved'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
