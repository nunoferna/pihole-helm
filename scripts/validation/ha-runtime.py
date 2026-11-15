#!/usr/bin/env python3
"""Disposable kind-only VIP crash, bounded load, and encrypted recovery checks.

Requires a three-node kind cluster, MetalLB native manifests installed, Helm,
Docker, kubectl, age, PyYAML. Refuses any existing fixture namespace and validates
kind container labels before killing a node. Never targets a homelab node.
"""
import argparse
import base64
import copy
import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASELINE = json.loads((ROOT / 'charts/pihole/files/compatibility.json').read_text())
IMAGE = 'pihole/pihole:' + BASELINE['dockerTag'] + '@' + BASELINE['digest']
CLIENT_IMAGE = 'python:3.13-alpine@sha256:8bcfa47b04b853a17aaf909362b847b2112fd6ec4105ea553ef9a43bafbb5916'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--kubeconfig', type=Path, required=True)
    parser.add_argument('--docker-context', required=True)
    parser.add_argument('--cluster-name', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--list-domains', type=int, default=100000)
    parser.add_argument('--chart', type=Path, default=ROOT / 'charts/pihole')
    args = parser.parse_args()
    if args.context != 'kind-' + args.cluster_name:
        parser.error('Context must match the disposable kind cluster')
    lock_path = Path(tempfile.gettempdir()) / ('pihole-ha-' + hashlib.sha256(args.cluster_name.encode()).hexdigest()[:12] + '.lock')
    lock = lock_path.open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('Another HA fixture is still using this disposable cluster, including cleanup')
    os.environ['KUBECONFIG'] = str(args.kubeconfig)
    namespace = 'pihole-ha-' + uuid.uuid4().hex[:8]
    restored = namespace + '-restore'
    report = {'startedUTC': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'context': args.context,
              'scope': 'disposable kind L2/Local VIP, one abrupt worker-container failure, bounded synthetic load and fresh-namespace recovery; not hardware/site certification',
              'images': {'pihole': IMAGE, 'client': CLIENT_IMAGE}, 'checks': []}
    report['tested_chart'] = str(args.chart)
    if args.chart.is_file():
        report['tested_archive_sha256'] = hashlib.sha256(args.chart.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(32)
    created, clients, stopped = [], [], None
    def run(command, input=None, check=True, timeout=240):
        result = subprocess.run(command, input=input, text=True, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout)[-2200:].replace(password, '[REDACTED]'))
        return result
    def kube(*parts, input=None, check=True):
        return run(['kubectl', '--kubeconfig', str(args.kubeconfig), '--context', args.context, *parts], input, check)
    def docker(*parts, check=True):
        return run(['docker', '--context', args.docker_context, *parts], check=check)
    def get(kind, name, ns=namespace):
        return json.loads(kube('-n', ns, 'get', kind, name, '-o', 'json').stdout)
    def execute(*parts, ns=namespace, pod='pihole-ha-0', check=True):
        return kube('-n', ns, 'exec', pod, '--', *parts, check=check)
    def require(condition, message):
        if not condition:
            raise AssertionError(message)
    def save():
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    def record(name, function):
        start = time.monotonic()
        details = function()
        result = {'check': name, 'passed': True, 'seconds': round(time.monotonic() - start, 2), 'details': details}
        report['checks'].append(result); save(); print(json.dumps(result), flush=True)
        return details
    def provision(ns):
        require(kube('get', 'namespace', ns, check=False).returncode != 0, 'Fixture namespace already exists')
        kube('create', 'namespace', ns); created.append(ns)
        kube('-n', ns, 'create', '-f', '-', input=json.dumps({'apiVersion': 'v1', 'kind': 'Secret',
             'metadata': {'name': 'web'}, 'stringData': {'password': password}}))
        kube('-n', ns, 'create', '-f', '-', input=json.dumps({'apiVersion': 'v1', 'kind': 'Secret',
             'metadata': {'name': 'admin-tls-v1'}, 'immutable': True,
             'stringData': {'admin.pem': tls_pem, 'ca.crt': tls_certificate}}))
    def install(values, ns):
        with tempfile.TemporaryDirectory(prefix='pihole-ha-values-') as directory:
            path = Path(directory) / 'values.yaml'; path.write_text(yaml.safe_dump(values))
            run(['helm', 'upgrade', '--install', 'pihole-ha', str(args.chart), '--kube-context', args.context,
                 '-n', ns, '-f', str(path), '--wait', '--timeout', '4m'], timeout=270)
    def client(vip, duration=20, workers=1, samples=False):
        name = 'pihole-dns-client-' + uuid.uuid4().hex[:10]
        clients.append(name)
        command = ['run', '-d', '--name', name, '--network', 'kind', '--entrypoint', 'python3', CLIENT_IMAGE,
                   '-u', '-c', (ROOT / 'scripts/validation/dns-load.py').read_text(), '--server', vip,
                   '--record', 'health.ha.invalid=192.0.2.42', '--record', 'blocked.ha.invalid=0.0.0.0',
                   '--duration', str(duration), '--workers', str(workers), '--interval', '0.02' if workers > 1 else '0.1']
        if samples:
            command += ['--samples']
        docker(*command)
        return name
    def finish_client(name):
        code = int(docker('wait', name).stdout.strip())
        entries = [json.loads(line) for line in docker('logs', name).stdout.splitlines() if line.startswith('{')]
        return code, entries
    def tls_check(ns=namespace):
        base = ['curl', '--silent', '--show-error', '--fail', '--location', '--max-time', '10',
                '--cacert', '/certs/ca.crt', '--output', '/dev/null']
        execute(*base, '--resolve', 'admin.fixture.invalid:8443:127.0.0.1', 'https://admin.fixture.invalid:8443/admin/', ns=ns)
        service_ip = get('service', 'pihole-ha-web', ns=ns)['spec']['clusterIP']
        client_script = '''import socket, ssl, time
address = %r
trusted = ssl.create_default_context(cafile='/ca/ca.crt')
for attempt in range(20):
    try:
        raw = socket.create_connection((address, 443), timeout=2)
        break
    except OSError:
        if attempt == 19: raise
        time.sleep(.5)
with trusted.wrap_socket(raw, server_hostname='admin.fixture.invalid') as connection:
    connection.sendall(b'GET /admin/ HTTP/1.1\\r\\nHost: admin.fixture.invalid\\r\\nConnection: close\\r\\n\\r\\n')
    response = connection.recv(4096)
    assert int(response.split(b' ')[1]) in (200, 301, 302, 303, 307, 308), response[:100]
for context, name in [(trusted, 'wrong.fixture.invalid'), (ssl.create_default_context(), 'admin.fixture.invalid')]:
    try:
        with socket.create_connection((address, 443), timeout=2) as raw:
            with context.wrap_socket(raw, server_hostname=name): pass
    except ssl.SSLCertVerificationError: pass
    else: raise AssertionError('Invalid TLS trust/hostname was accepted')
print('Service TLS, CA trust and hostname checks passed')
''' % service_ip
        probe = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'admin-tls-probe'}, 'spec': {
            'restartPolicy': 'Never', 'automountServiceAccountToken': False,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'client', 'image': CLIENT_IMAGE, 'command': ['python3', '-c', client_script],
                            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                            'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '96Mi'}},
                            'volumeMounts': [{'name': 'ca', 'mountPath': '/ca', 'readOnly': True}]}],
            'volumes': [{'name': 'ca', 'secret': {'secretName': 'admin-tls-v1', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}}
        kube('-n', ns, 'create', '-f', '-', input=json.dumps(probe))
        result = kube('-n', ns, 'wait', '--for=jsonpath={.status.phase}=Succeeded', 'pod/admin-tls-probe', '--timeout=90s', check=False)
        require(result.returncode == 0, 'Separate admin TLS client failed: ' + kube('-n', ns, 'logs', 'admin-tls-probe', check=False).stdout[-1500:])
        wrong_host = execute(*base, '--resolve', 'wrong.fixture.invalid:8443:127.0.0.1',
                             'https://wrong.fixture.invalid:8443/admin/', ns=ns, check=False)
        require(wrong_host.returncode == 60, 'TLS hostname mismatch was not rejected')
        untrusted = execute('curl', '--silent', '--show-error', '--fail', '--max-time', '10', '--output', '/dev/null',
                            '--resolve', 'admin.fixture.invalid:8443:127.0.0.1', 'https://admin.fixture.invalid:8443/admin/', ns=ns, check=False)
        require(untrusted.returncode == 60, 'Untrusted admin certificate was not rejected')
        return {'customSecretPEM': True, 'trustedCAAndHostnameValidated': True, 'service443ToPod8443Validated': True,
                'wrongHostnameAndUntrustedCARejected': True}
    try:
        nodes = json.loads(kube('get', 'nodes', '-o', 'json').stdout)['items']
        workers = [n['metadata']['name'] for n in nodes if 'node-role.kubernetes.io/control-plane' not in n['metadata'].get('labels', {})]
        require(len(workers) >= 2, 'At least two disposable worker nodes are required')
        for node in nodes:
            descriptor = json.loads(docker('inspect', node['metadata']['name']).stdout)[0]
            require(descriptor['Config']['Labels'].get('io.x-k8s.kind.cluster') == args.cluster_name, 'Node is not owned by the specified disposable kind cluster')
        report['nodes'] = [{'name': n['metadata']['name'], 'info': n['status']['nodeInfo']} for n in nodes]
        for worker in workers:
            kube('label', 'node', worker, 'pihole-validation=true', '--overwrite')
        kube('-n', 'metallb-system', 'rollout', 'status', 'deployment/controller', '--timeout=90s')
        kube('-n', 'metallb-system', 'rollout', 'status', 'daemonset/speaker', '--timeout=90s')
        network = json.loads(docker('network', 'inspect', 'kind').stdout)[0]
        subnet = next(ipaddress.ip_network(c['Subnet']) for c in network['IPAM']['Config'] if ':' not in c['Subnet'])
        require(subnet.num_addresses >= 1024, 'Disposable Docker network is too small for the reserved test VIP range')
        first, last = str(subnet[-100]), str(subnet[-90])
        pool_name = 'pihole-' + uuid.uuid4().hex[:8]
        kube('apply', '-f', '-', input=json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': [
            {'apiVersion': 'metallb.io/v1beta1', 'kind': 'IPAddressPool', 'metadata': {'name': pool_name, 'namespace': 'metallb-system'},
             'spec': {'addresses': [first + '-' + last]}},
            {'apiVersion': 'metallb.io/v1beta1', 'kind': 'L2Advertisement', 'metadata': {'name': pool_name, 'namespace': 'metallb-system'},
             'spec': {'ipAddressPools': [pool_name]}}
        ]}))
        with tempfile.TemporaryDirectory(prefix='pihole-admin-tls-') as directory:
            cert, key = Path(directory) / 'tls.crt', Path(directory) / 'tls.key'
            ca, ca_key = Path(directory) / 'ca.crt', Path(directory) / 'ca.key'
            csr, extensions = Path(directory) / 'tls.csr', Path(directory) / 'tls.ext'
            run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-subj', '/CN=Disposable Pi-hole test CA', '-addext', 'basicConstraints=critical,CA:TRUE',
                 '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', str(ca_key), '-out', str(ca)])
            run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=admin.fixture.invalid',
                 '-keyout', str(key), '-out', str(csr)])
            extensions.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:admin.fixture.invalid\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
            run(['openssl', 'x509', '-req', '-in', str(csr), '-CA', str(ca), '-CAkey', str(ca_key), '-CAcreateserial',
                 '-days', '1', '-sha256', '-extfile', str(extensions), '-out', str(cert)])
            tls_certificate = ca.read_text(); tls_pem = key.read_text() + cert.read_text() + tls_certificate
        provision(namespace)
        body = 'blocked.ha.invalid\n' + ''.join(f'deny-{i:06}.invalid\n' for i in range(1, args.list_domains + 1))
        checksum = hashlib.sha256(body.encode()).hexdigest()
        seed = 'set -eu; : > /etc/pihole/adlists.list; mkdir -p /etc/pihole/helm-snapshots; { printf "blocked.ha.invalid\\n"; '
        seed += "awk 'BEGIN {for(i=1;i<=" + str(args.list_domains) + ";i++) printf \"deny-%06d.invalid\\n\",i}'; } > /etc/pihole/helm-snapshots/" + checksum + '.txt'
        values = {'fullnameOverride': 'pihole-ha', 'replicas': 2,
                  'pihole': {'web': {'existingSecret': 'web'}, 'lists': {'adlists': [
                      {'address': 'https://unavailable.fixture.invalid/list.txt', 'sha256': checksum}]},
                      'ftl': {'dns': {'hosts': ['192.0.2.42 health.ha.invalid', '192.0.2.43 blocked.ha.invalid'], 'upstreams': ['192.0.2.53'],
                                      'rateLimit': {'count': 0}}, 'webserver': {'tls': {'cert': '/certs/admin.pem'}}}},
                  'podSecurityContext': {'fsGroup': 1000},
                  'extraVolumes': [{'name': 'admin-tls', 'secret': {'secretName': 'admin-tls-v1', 'defaultMode': 0o440}}],
                  'extraVolumeMounts': [{'name': 'admin-tls', 'mountPath': '/certs', 'readOnly': True}],
                  'policy': {'requirePinnedLists': True}, 'probes': {'dns': {'name': 'health.ha.invalid', 'expectedAddress': '192.0.2.42'}},
                  'validation': {'blockedDomain': 'blocked.ha.invalid'}, 'metrics': {'enabled': True},
                  'services': {'dns': {'type': 'LoadBalancer', 'externalTrafficPolicy': 'Local', 'annotations': {'metallb.io/address-pool': pool_name}}},
                  'nodeSelector': {'pihole-validation': 'true'}, 'podDisruptionBudget': {'enabled': True, 'minAvailable': 1},
                  'affinity': {'podAntiAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': [{'topologyKey': 'kubernetes.io/hostname',
                       'labelSelector': {'matchLabels': {'app.kubernetes.io/instance': 'pihole-ha', 'app.kubernetes.io/component': 'dns'}}}]}},
                  'persistence': {'etcPihole': {'size': '1Gi'}}, 'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
                  'networking': {'hostAliases': [{'ip': '192.0.2.1', 'hostnames': ['raw.githubusercontent.com', 'github.com']}]},
                  'extraInitContainers': [{'name': 'fixture-snapshot', 'image': IMAGE, 'command': ['sh', '-ec', seed],
                       'volumeMounts': [{'name': 'etc-pihole', 'mountPath': '/etc/pihole'}]}]}
        record('two_replica_offline_snapshot_startup', lambda: (install(values, namespace) or {'domains': args.list_domains + 1, 'sha256': checksum}))
        record('private_admin_tls_and_negative_validation', tls_check)
        run(['helm', 'test', 'pihole-ha', '--kube-context', args.context, '-n', namespace, '--timeout', '2m'])
        service = get('service', 'pihole-ha-dns')
        vip = service['status']['loadBalancer']['ingress'][0]['ip']; report['VIP'] = vip
        def client_policy():
            name = 'pihole-policy-client-' + uuid.uuid4().hex[:10]
            clients.append(name)
            docker('run', '-d', '--name', name, '--network', 'kind', '--entrypoint', 'sleep', CLIENT_IMAGE, '600')
            descriptor = json.loads(docker('inspect', name).stdout)[0]
            address = descriptor['NetworkSettings']['Networks']['kind']['IPAddress']
            warmup = []
            def check(expected):
                for attempt in range(10):
                    result = docker('exec', name, 'python3', '-c', (ROOT / 'scripts/validation/dns-load.py').read_text(),
                        '--server', vip, '--record', 'blocked.ha.invalid=' + expected, '--duration', '1', check=False)
                    summary = json.loads(result.stdout.splitlines()[-1])['summary']
                    if result.returncode == 0 and summary['failures'] == 0:
                        return
                    # Allow bounded initial L2/Service transport convergence,
                    # retaining observations. Wrong answers are never retried.
                    errors = summary['firstErrors']
                    require(errors and all(e['error'] == 'timed out' or e['error'].startswith('[Errno ') for e in errors),
                            'Client-specific filtering differs: ' + json.dumps(summary))
                    warmup.append(summary)
                    time.sleep(.5)
                raise AssertionError('External VIP transport did not converge before the client-policy deadline')
            check('0.0.0.0')
            values['pihole']['groups'] = [{'name': 'source-ip-fixture'}]
            values['pihole']['clients'] = [{'id': address, 'groups': ['source-ip-fixture']}]
            install(values, namespace)
            check('192.0.2.43')
            values['pihole']['clients'] = []; values['pihole']['groups'] = []
            install(values, namespace)
            check('0.0.0.0')
            return {'externalClientIP': address, 'defaultGroupBlocked': True, 'explicitClientGroupBypassesDefaultList': True,
                    'ownedClientGroupRemovalRestoresBlocking': True, 'udpAndTcp': True,
                    'transportWarmupObservations': warmup,
                    'sourcePreservationScope': 'This MetalLB L2 externalTrafficPolicy=Local fixture only'}
        record('external_source_ip_and_client_specific_filtering', client_policy)
        def load():
            code, entries = finish_client(client(vip, duration=20, workers=4))
            summary = entries[-1]['summary']
            report['baselineLoadObservation'] = summary; save()
            require(code == 0 and summary['failures'] == 0, 'Baseline external UDP/TCP load returned failures')
            return {'externalClient': 'Docker peer on the kind bridge, outside Kubernetes', 'results': summary,
                    'storage': execute('sh', '-c', 'du -sk /etc/pihole; df -k /dev/shm; grep VmRSS /proc/$(pidof pihole-FTL)/status').stdout,
                    'limit': 'Synthetic list and one client with per-client rate limiting disabled for throughput measurement; no long-term history or saturation/SLA claim'}
        record('bounded_external_load', load)
        def crash():
            nonlocal stopped
            statuses = json.loads(kube('-n', 'metallb-system', 'get', 'servicel2statuses', '-o', 'json').stdout)['items']
            announcers = [s['status']['node'] for s in statuses if s.get('status', {}).get('serviceNamespace') == namespace and s['status'].get('serviceName') == 'pihole-ha-dns']
            require(len(set(announcers)) == 1 and announcers[0] in workers, 'Expected exactly one DNS worker announcing the Local VIP')
            dns_pods = json.loads(kube('-n', namespace, 'get', 'pods', '-l', 'app.kubernetes.io/component=dns', '-o', 'json').stdout)['items']
            crashed_pod = next(p['metadata']['name'] for p in dns_pods if p['spec']['nodeName'] == announcers[0])
            stopped = announcers[0]
            sampler = client(vip, duration=40, samples=True)
            time.sleep(3)
            killed_at = time.time(); docker('kill', stopped)
            _, entries = finish_client(sampler)
            samples = [e['sample'] for e in entries if 'sample' in e]
            before = [s for s in samples if s['utc'] + s['ms'] / 1000 < killed_at]
            after = [s for s in samples if s['utc'] >= killed_at]
            require(before and all(s['ok'] for s in before), 'External baseline was not healthy before the crash')
            failures = [s for s in after if not s['ok']]
            last_failure = max((s['utc'] + s['ms'] / 1000 for s in failures), default=killed_at)
            first_recovered = min((s['utc'] for s in after if s['ok'] and s['utc'] >= last_failure), default=None)
            steady = [s for s in after if s['utc'] > last_failure + 2]
            require(len(steady) >= 20 and all(s['ok'] for s in steady), 'VIP did not recover and remain correct before the deadline')
            require({s['protocol'] for s in steady} == {'udp', 'tcp'}, 'Recovered stream omitted a DNS transport')
            docker('start', stopped); stopped = None
            # Ready can still reflect the pre-crash heartbeat. Require a real
            # kubelet exec connection before trusting controller status again.
            reconnect_start = time.monotonic()
            while execute('test', '-f', '/etc/pihole/gravity.db', pod=crashed_pod, check=False).returncode:
                require(time.monotonic() - reconnect_start < 90, 'Restarted worker kubelet did not reconnect')
                time.sleep(1)
            kube('wait', '--for=condition=Ready', 'node/' + announcers[0], '--timeout=120s')
            kube('-n', namespace, 'rollout', 'status', 'statefulset/pihole-ha', '--timeout=120s')
            run(['helm', 'test', 'pihole-ha', '--kube-context', args.context, '-n', namespace, '--timeout', '2m'])
            return {'killedKindWorker': announcers[0], 'mechanism': 'SIGKILL of the Docker node container',
                    'postCrashQueries': len(after), 'failedQueries': len(failures),
                    'lastObservedFailureAfterSeconds': round(last_failure - killed_at, 3), 'steadyRecoverySamples': len(steady),
                    'firstSuccessAfterFinalFailureSeconds': round(first_recovered - killed_at, 3) if first_recovered else None,
                    'restartedNodePodReconnected': crashed_pod, 'perReplicaPostRestartHelmTests': True,
                    'summary': entries[-1]['summary'], 'sampleIntervalSeconds': .1,
                    'limit': 'One local virtual worker and L2 VIP; not physical power loss, CNI/provider-independent guarantees or site recovery'}
        record('abrupt_announcer_loss_and_udp_tcp_recovery', crash)
        def recovery():
            provision(restored)
            pvc = 'etc-pihole-pihole-ha-0'
            kube('-n', restored, 'create', '-f', '-', input=json.dumps({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
                 'metadata': {'name': pvc}, 'spec': {'accessModes': ['ReadWriteOnce'], 'storageClassName': 'standard', 'resources': {'requests': {'storage': '1Gi'}}}}))
            original = json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json').stdout)
            directory = Path(tempfile.mkdtemp(prefix='pihole-encrypted-recovery-'))
            key, archive = directory / 'identity', directory / 'backup.age'
            run(['age-keygen', '-o', str(key)])
            recipient = run(['age-keygen', '-y', str(key)]).stdout.strip()
            backup = json.loads(run(['python3', str(ROOT / 'scripts/recovery.py'), 'backup', '--context', args.context, '--namespace', namespace,
                 '--pod', 'pihole-ha-0', '--recipient', recipient, '--output', str(archive)]).stdout)
            require(archive.read_bytes().startswith(b'age-encryption.org/v1'), 'Backup is not age-encrypted')
            result = run(['python3', str(ROOT / 'scripts/recovery.py'), 'restore', '--context', args.context, '--namespace', restored,
                          '--pvc', pvc, '--identity', str(key), '--input', str(archive), '--expected-sha256', backup['sha256']])
            target = copy.deepcopy(values)
            target['replicas'] = 1; target.pop('extraInitContainers')
            target['policy']['owner'] = namespace + '/pihole-ha'
            target['services'] = {'dns': {'type': 'ClusterIP'}}
            install(target, restored)
            actual = json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json', ns=restored).stdout)
            require(actual['entries'] == original['entries'] and actual['owner'] == original['owner'], 'Restored policy changed ownership identities')
            run(['helm', 'test', 'pihole-ha', '--kube-context', args.context, '-n', restored, '--timeout', '2m'])
            tls_check(restored)
            refused = run(['python3', str(ROOT / 'scripts/recovery.py'), 'restore', '--context', args.context, '--namespace', restored,
                           '--pvc', pvc, '--identity', str(key), '--input', str(archive), '--expected-sha256', backup['sha256']], check=False)
            require(refused.returncode != 0 and 'referenced by a pod' in refused.stderr, 'Restore did not protect the active claim')
            mismatch = run(['python3', str(ROOT / 'scripts/recovery.py'), 'restore', '--context', args.context, '--namespace', restored,
                           '--pvc', pvc, '--identity', str(key), '--input', str(archive), '--expected-sha256', '0' * 64], check=False)
            require(mismatch.returncode != 0 and 'expected SHA-256' in mismatch.stderr, 'Restore accepted unverified backup bytes')
            return {'backupAndIdentityDirectory': str(directory), 'archiveSHA256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                    'restoredIntoFreshPVCAndNamespace': True, 'identicalOwnershipEntries': True, 'listProviderUnavailableThroughout': True,
                    'perReplicaUDPAndTCPFilteringAuthenticationMetrics': True, 'activeClaimOverwriteRejected': True,
                    'adminTLSAndTrustRestoredFromIndependentSecret': True,
                    'independentCiphertextChecksumEnforced': True,
                    'queryHistoryIncluded': False, 'restore': json.loads(result.stdout)}
        record('encrypted_off_cluster_backup_and_fresh_namespace_restore', recovery)
    except Exception as error:
        report['error'] = str(error).replace(password, '[REDACTED]')[-3000:]
        print('ERROR ' + report['error'], flush=True)
        for ns in created:
            pod = kube('-n', ns, 'logs', 'pihole-ha-0', '--tail=15', check=False)
            report.setdefault('diagnostics', {})[ns] = pod.stdout[-2000:].replace(password, '[REDACTED]')
    finally:
        if stopped:
            docker('start', stopped, check=False)
        for name in clients:
            docker('rm', '-f', name, check=False)
        for ns in reversed(created):
            result = kube('delete', 'namespace', ns, '--timeout=90s', check=False)
            report.setdefault('namespaceCleanup', {})[ns] = result.returncode == 0
        if 'pool_name' in locals():
            kube('-n', 'metallb-system', 'delete', 'l2advertisement,ipaddresspool', pool_name, check=False)
        report['finishedUTC'] = datetime.datetime.now(datetime.timezone.utc).isoformat(); save()
    if report.get('error'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
