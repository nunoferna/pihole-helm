#!/usr/bin/env python3
"""Run policy lifecycle fixtures in a new isolated homelab namespace; delete it afterward."""
import argparse
import base64
import copy
import datetime
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = None
NS = 'pihole-policy-validation'
RELEASE = 'policy-audit'
POD = 'policy-audit-pihole-0'
BASELINE = json.loads((ROOT / "charts/pihole/files/compatibility.json").read_text())
IMAGE = "pihole/pihole:" + BASELINE["dockerTag"] + "@" + BASELINE["digest"]
REPORT = ROOT / 'docs/validation/policy-runtime.json'
report = {'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'context': CONTEXT,
          'namespace': NS, 'scope': 'isolated pinned-image API policy lifecycle and retained-artifact rollback; no production HA claim', 'checks': []}


def run(args, input=None, check=True, timeout=240):
    r = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(args[0] + ' failed: ' + r.stderr[-1500:])
    return r


def kube(*args, input=None, check=True):
    return run(['kubectl', '--context', CONTEXT, *args], input, check)


def execute(*args, input=None, check=True):
    return kube('-n', NS, 'exec', '-i', POD, '--', *args, input=input, check=check)


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def save():
    REPORT.write_text(json.dumps(report, indent=2) + '\n')


def record(name, function):
    start = time.monotonic()
    details = function()
    report['checks'].append({'check': name, 'passed': True, 'seconds': round(time.monotonic()-start, 2), 'details': details})
    save()
    print(json.dumps(report['checks'][-1]), flush=True)


def api(method, path, body=None):
    script = '''set -eu
api=http://127.0.0.1:8080/api
sid=$(jq -nc --rawfile password /etc/pihole/cli_pw '{password:($password|rtrimstr("\\n"))}' | curl -fsS --max-time 5 -H 'Content-Type: application/json' --data-binary @- "$api/auth" | jq -er .session.sid)
trap 'curl -fsS --max-time 3 -X DELETE -H "sid: $sid" "$api/auth" >/dev/null' EXIT
if [ "$1" = GET ] || [ "$1" = DELETE ]; then
  curl -fsS --max-time 10 -X "$1" -H "sid: $sid" "$api/$2"
else
  printf '%s' "$3" | base64 -d | curl -fsS --max-time 10 -X "$1" -H "sid: $sid" -H 'Content-Type: application/json' --data-binary @- "$api/$2"
fi'''
    encoded = base64.b64encode(json.dumps(body).encode()).decode()
    text = execute('bash', '-c', script, 'bash', method, path, encoded).stdout
    return json.loads(text) if text else None


def policy():
    return {'formatVersion': 1, 'owner': NS + '/' + RELEASE, 'mode': 'reconcile', 'adoptExisting': False,
            'groups': [], 'clients': [], 'lists': {'adlists': [], 'whitelist': [], 'blacklist': [], 'regex_whitelist': [], 'regex_blacklist': []}}


def apply(value, expected=True, revision=None, ca=None):
    raw = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    execute('sh', '-c', 'printf "%s" "$1" | base64 -d > /tmp/policy-input.json', 'sh', base64.b64encode(raw).decode())
    env = ['env', 'HELM_POLICY_FILE=/tmp/policy-input.json', 'HELM_POLICY_EXPECTED_REVISION=' + (revision or digest)]
    if ca:
        env += ['CURL_CA_BUNDLE=' + ca]
    r = execute(*env, 'bash', '/etc/pihole-helm/import-lists.sh', check=False)
    if expected and r.returncode:
        log = execute('tail', '-15', '/etc/pihole/helm-import.log').stdout
        raise AssertionError('Policy apply failed: ' + log[-1600:])
    if not expected:
        require(r.returncode != 0, 'invalid/unsafe policy unexpectedly applied')
        require(execute('test', '-e', '/run/pihole-helm-policy-ready', check=False).returncode != 0, 'failed apply published readiness')
    else:
        require(execute('cat', '/run/pihole-helm-policy-ready').stdout.strip() == digest, 'readiness revision differs')
    return digest


def dns(name):
    return execute('dig', '+short', '+time=1', '+tries=1', '@127.0.0.1', '-p', '5353', name).stdout.strip()


def main():
    global REPORT, CONTEXT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--functional-only', action='store_true')
    parser.add_argument('--context', required=True, help='Explicit cluster context; fixture refuses an existing namespace')
    parser.add_argument('--chart', type=Path, default=ROOT / 'charts/pihole', help='Verified packaged chart or chart directory to exercise')
    parser.add_argument('--output', type=Path, default=REPORT)
    parser.add_argument('--upgrade-from', type=Path, help='Known previous compatibility record for a retained-volume container image upgrade fixture')
    args = parser.parse_args()
    CONTEXT = args.context
    report['context'] = CONTEXT
    REPORT = args.output
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    report['images'] = {'pihole': IMAGE, 'exporter': 'ekofr/pihole-exporter:' + BASELINE['exporterTag'] + '@' + BASELINE['exporterDigest']}
    report['helm_version'] = run(['helm', 'version', '--short']).stdout.strip()
    report['tested_chart'] = str(args.chart)
    if args.chart.is_file():
        report['tested_archive_sha256'] = hashlib.sha256(args.chart.read_bytes()).hexdigest()
    report['network_policy_enforcement'] = 'Not asserted by this generic fixture; validate enforcement in a real-CNI deployment profile'
    created = False
    try:
        nodes = json.loads(kube('get', 'nodes', '-o', 'json').stdout)['items']
        report['nodes'] = [{'name': n['metadata']['name'], 'architecture': n['status']['nodeInfo']['architecture'],
                            'kubelet': n['status']['nodeInfo']['kubeletVersion']} for n in nodes]
        require(kube('get', 'namespace', NS, check=False).returncode != 0, 'fixture namespace already exists')
        kube('create', 'namespace', NS)
        created = True
        password = secrets.token_urlsafe(32)
        kube('create', '-f', '-', input=json.dumps({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'fixture-password', 'namespace': NS}, 'stringData': {'password': password}}))
        values = {'replicas': 1, 'persistence': {'etcPihole': {'enabled': False}}, 'pihole': {'web': {'existingSecret': 'fixture-password'}, 'ftl': {'dns': {'upstreams': ['192.0.2.53'], 'hosts': ['192.0.2.42 health.fixture.invalid', '192.0.2.43 owned.fixture.invalid', '192.0.2.44 manual.fixture.invalid']}}}, 'probes': {'dns': {'name': 'health.fixture.invalid', 'expectedAddress': '192.0.2.42'}}, 'metrics': {'enabled': True}, 'networkPolicy': {'enabled': True}, 'monitoring': {'syntheticDns': {'enabled': True, 'schedule': '0 0 1 1 *'}}, 'resources': {'limits': {'cpu': '1', 'memory': '512Mi'}}, 'networking': {'hostAliases': [{'ip': '192.0.2.1', 'hostnames': ['raw.githubusercontent.com', 'github.com']}]}, 'extraInitContainers': [{'name': 'seed', 'image': IMAGE, 'command': ['/bin/sh', '-c', ': > /etc/pihole/adlists.list'], 'volumeMounts': [{'name': 'etc-pihole', 'mountPath': '/etc/pihole'}]}], 'services': {'dns': {'type': 'ClusterIP'}}}
        if args.upgrade_from:
            previous = json.loads(args.upgrade_from.read_text())
            values['image'] = {'tag': previous['dockerTag'], 'digest': previous['digest'], 'allowUnverified': True}
            values['pihole']['lists'] = {'blacklist': [{'domain': 'owned.fixture.invalid'}]}
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'values.yaml'
            def upgrade():
                file.write_text(yaml.safe_dump(values))
                run(['helm', 'upgrade', '--install', RELEASE, str(args.chart), '--kube-context', CONTEXT, '-n', NS, '-f', str(file), '--wait', '--timeout', '3m'])
            upgrade()
            if args.upgrade_from:
                def image_upgrade():
                    require(execute('pihole-FTL', '--version').stdout.strip().lstrip('v') == previous['components']['ftl'], 'previous image did not start')
                    # New exporter must also work while an older server is still
                    # present during an ordinary independent rollout.
                    run(['helm', 'test', RELEASE, '--kube-context', CONTEXT, '-n', NS, '--timeout', '90s'])
                    old = json.loads(kube('-n', NS, 'get', 'pod', POD, '-o', 'json').stdout)
                    journal = json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json').stdout)
                    marker = secrets.token_hex(16)
                    execute('sh', '-ec', 'printf "%s" "$1" > /etc/pihole/.image-upgrade-proof; sync /etc/pihole/.image-upgrade-proof', 'sh', marker)
                    require(execute('cat', '/etc/pihole/.image-upgrade-proof').stdout == marker, 'upgrade marker write failed')
                    kube('-n', NS, 'patch', 'pod', POD, '--type=json', '-p', json.dumps([{'op': 'replace', 'path': '/spec/containers/0/image', 'value': IMAGE}]))
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        pod = json.loads(kube('-n', NS, 'get', 'pod', POD, '-o', 'json').stdout)
                        status = pod['status']['containerStatuses'][0]
                        image_id = status.get('imageID', '')
                        if status.get('ready') and any(d in image_id for d in (BASELINE['digest'], BASELINE['platformDigests']['linux/amd64'])):
                            break
                        time.sleep(2)
                    else:
                        raise AssertionError('updated container did not become ready within the upgrade deadline')
                    require(pod['metadata']['uid'] == old['metadata']['uid'], 'fixture replaced the pod/emptyDir instead of upgrading the container')
                    require(execute('cat', '/etc/pihole/.image-upgrade-proof').stdout == marker, 'persisted marker lost during image upgrade')
                    current = json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json').stdout)
                    require(current['entries'] == journal['entries'], 'policy ownership identity changed during image upgrade')
                    require(execute('pihole-FTL', 'sqlite3', '/etc/pihole/gravity.db', 'PRAGMA integrity_check;').stdout.strip() == 'ok', 'upgraded gravity database integrity failed')
                    require(dns('owned.fixture.invalid') == '0.0.0.0', 'persisted filtering lost after image upgrade')
                    values.pop('image')
                    values['pihole'].pop('lists')
                    upgrade()  # Reset to the clean current-image lifecycle fixture.
                    return {'from_tag': previous['dockerTag'], 'to_tag': BASELINE['dockerTag'], 'same_pod_volume_retained': True,
                            'ownership_and_filtering_retained': True, 'database_integrity': 'ok', 'new_exporter_authenticated_to_old_server': True,
                            'volume_scope': 'same-pod emptyDir retained across container restart; real PVC rollout is tested separately in staging'}
                record('previous_image_retained_data_upgrade', image_upgrade)
            def versions():
                actual = execute('pihole-FTL', '--version').stdout.strip()
                require(actual.lstrip('v') == BASELINE['components']['ftl'], 'running FTL differs from compatibility record')
                # /etc/pihole/versions is update-checker cache and may still be
                # empty during isolated/offline startup. Inspect installed code.
                installed = {'CORE_VERSION': execute('git', '-c', 'safe.directory=/etc/.pihole', '-C', '/etc/.pihole', 'describe', '--tags', '--always').stdout.strip(),
                             'WEB_VERSION': execute('git', '-c', 'safe.directory=/var/www/html/admin', '-C', '/var/www/html/admin', 'describe', '--tags', '--always').stdout.strip(),
                             'FTL_VERSION': actual}
                observed = {key: value.split('-', 1)[0].lstrip('v') for key, value in installed.items()}
                report['observed_component_versions'] = observed
                report['installed_component_descriptions'] = installed
                save()
                for key, component in [('CORE_VERSION', 'core'), ('WEB_VERSION', 'web'), ('FTL_VERSION', 'ftl')]:
                    require(observed.get(key) == BASELINE['components'][component], 'running component version differs: ' + key)
                return {'ftl_binary': actual, 'component_versions': observed, 'installed_descriptions': installed}
            record('pinned_runtime_component_versions', versions)
            record('empty_policy_initial_hook', lambda: {'revision': execute('cat', '/run/pihole-helm-policy-ready').stdout.strip(), 'owned_count': len(json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json').stdout)['entries'])})
            if args.functional_only:
                record('initial_helm_functional_test', lambda: (run(['helm', 'test', RELEASE, '--kube-context', CONTEXT, '-n', NS, '--timeout', '90s']) and {'passed': True}))
                return
            original_artifact = json.loads(kube('-n', NS, 'get', 'statefulset', 'policy-audit-pihole', '-o', 'json').stdout)['spec']['template']['metadata']['annotations']['pihole-helm/policy-artifact']
            baseline_revision = str(json.loads(run(['helm', 'status', RELEASE, '--kube-context', CONTEXT, '-n', NS, '-o', 'json']).stdout)['version'])
            api('POST', 'domains/deny/exact', {'domain': 'manual.fixture.invalid', 'comment': 'unmanaged sentinel', 'groups': [0]})
            desired = policy()
            desired['lists']['blacklist'] = [{'domain': 'owned.fixture.invalid', 'comment': "literal ' quote $(false) `false`"}]
            def create_update_delete():
                apply(desired)
                require(dns('owned.fixture.invalid') == '0.0.0.0', 'owned rule did not block')
                changed = copy.deepcopy(desired); changed['lists']['blacklist'][0]['enabled'] = False
                apply(changed)
                require(dns('owned.fixture.invalid') == '192.0.2.43', 'update did not disable blocking')
                apply(policy())
                domains = api('GET', 'domains/deny/exact')['domains']
                require([d['domain'] for d in domains] == ['manual.fixture.invalid'], 'empty policy deleted unmanaged data or retained owned data')
                return {'create_update_remove_last_owned_rule': True, 'unmanaged_rule_retained': True}
            record('ownership_crud_and_empty_policy', create_update_delete)
            def expired_session():
                wrapper = '''#!/bin/bash
set -eu
args=("$@")
method=""; header=""
for ((i=0;i<${#args[@]};i++)); do
    if [[ "${args[i]}" == -X ]]; then method=${args[i+1]}; fi
    if [[ "${args[i]}" == -H && "${args[i+1]}" == sid:* ]]; then header=${args[i+1]}; fi
done
if [[ "$method" == POST && "${args[-1]}" == */api/domains/deny/exact && ! -e /tmp/expire-session-once ]]; then
    touch /tmp/expire-session-once
    /usr/bin/curl -fsS --max-time 3 -X DELETE -H "$header" http://127.0.0.1:8080/api/auth >/dev/null
fi
exec /usr/bin/curl "$@"
'''
                execute('sh', '-ec', 'mkdir -p /tmp/expire-session-bin; printf "%s" "$1" | base64 -d > /tmp/expire-session-bin/curl; chmod 755 /tmp/expire-session-bin/curl; rm -f /tmp/expire-session-once',
                        'sh', base64.b64encode(wrapper.encode()).decode())
                value = policy(); value['lists']['blacklist'] = [{'domain': 'session.fixture.invalid'}]
                raw = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
                digest = hashlib.sha256(raw).hexdigest()
                execute('sh', '-ec', 'printf "%s" "$1" | base64 -d > /tmp/policy-input.json', 'sh', base64.b64encode(raw).decode())
                execute('env', 'PATH=/tmp/expire-session-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                        'HELM_POLICY_FILE=/tmp/policy-input.json', 'HELM_POLICY_EXPECTED_REVISION=' + digest,
                        'bash', '/etc/pihole-helm/import-lists.sh')
                entries = api('GET', 'domains/deny/exact')['domains']
                require(sum(e['domain'] == 'session.fixture.invalid' for e in entries) == 1, 'reauthentication duplicated or lost the mutation')
                require(dns('session.fixture.invalid') == '0.0.0.0', 'reauthenticated mutation did not filter')
                require(execute('cat', '/run/pihole-helm-policy-ready').stdout.strip() == digest, 'reauthentication lost policy readiness')
                apply(policy())
                return {'live_sid_revoked_before_post': True, 'bounded_reauthentication': True,
                        'exactly_one_created_rule': True, 'filtering_and_readiness': True}
            record('expired_api_session_reauthentication', expired_session)
            def collision():
                bad = policy(); bad['groups'] = [{'name': 'must-not-create'}]; bad['lists']['blacklist'] = [{'domain': 'manual.fixture.invalid'}]
                apply(bad, expected=False)
                require(not any(g['name'] == 'must-not-create' for g in api('GET', 'groups')['groups']), 'collision was not rejected before mutation')
                bad['adoptExisting'] = True; apply(bad)
                bad['adoptExisting'] = False; apply(bad)
                apply(policy())
                require(api('GET', 'domains/deny/exact')['domains'] == [], 'adopted entry was not subsequently removed')
                return {'collision_preflight': True, 'explicit_adoption_and_later_deletion': True}
            record('unmanaged_collision_and_explicit_adoption', collision)
            def associations():
                value = policy(); value['groups'] = [{'name': 'managed-group'}]
                value['clients'] = [{'id': '192.0.2.77', 'groups': ['managed-group']}]
                value['lists']['blacklist'] = [{'domain': 'owned.fixture.invalid', 'groups': ['managed-group']}]
                apply(value)
                gid = next(g['id'] for g in api('GET', 'groups')['groups'] if g['name'] == 'managed-group')
                require(api('GET', 'domains/deny/exact')['domains'][0]['groups'] == [gid], 'rule group association differs')
                require(api('GET', 'clients')['clients'][0]['groups'] == [gid], 'client group association differs')
                api('POST', 'domains/allow/exact', {'domain': 'unmanaged-dependency.invalid', 'groups': [gid]})
                apply(policy(), expected=False)
                require(any(g['id'] == gid for g in api('GET', 'groups')['groups']), 'group deletion cascaded into unmanaged record')
                api('DELETE', 'domains/allow/exact/unmanaged-dependency.invalid')
                apply(policy())
                require(any(g['id'] == 0 for g in api('GET', 'groups')['groups']), 'Default group deleted')
                return {'explicit_associations': True, 'unmanaged_dependency_guard': True, 'default_group_preserved': True}
            record('groups_clients_and_dependency_protection', associations)
            def subscriptions():
                value = policy()
                value['lists']['adlists'] = [{'address': 'https://lists.fixture.invalid/disabled.txt', 'type': kind, 'enabled': False} for kind in ('allow', 'block')]
                apply(value)
                for kind in ('allow', 'block'):
                    entries = api('GET', 'lists?type=' + kind)['lists']
                    require(any(e['address'].endswith('/disabled.txt') and not e['enabled'] for e in entries), 'subscription type/enabled not retained')
                # Make any gravity invocation fail. A no-op reconciliation must
                # retain the last good database without touching the provider.
                execute('sh', '-ec', 'mkdir -p /tmp/no-gravity; printf "#!/bin/sh\\nexit 97\\n" > /tmp/no-gravity/pihole; chmod 755 /tmp/no-gravity/pihole')
                raw = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
                digest = hashlib.sha256(raw).hexdigest()
                execute('env', 'PATH=/tmp/no-gravity:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                        'HELM_POLICY_FILE=/tmp/policy-input.json', 'HELM_POLICY_EXPECTED_REVISION=' + digest,
                        'bash', '/etc/pihole-helm/import-lists.sh')
                require(execute('cat', '/run/pihole-helm-policy-ready').stdout.strip() == digest, 'no-op reconciliation lost readiness')
                apply(policy())
                journal = json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json').stdout)
                require(not journal['gravityDirty'], 'gravity did not clear pending state')
                return {'allow_and_block_subscriptions': True, 'last_list_removal_rebuilds_gravity': True, 'unchanged_lists_do_not_invoke_gravity': True}
            record('subscription_types_and_gravity_state', subscriptions)
            def pinned_snapshots():
                fixture_image = 'python:3.13-alpine@sha256:8bcfa47b04b853a17aaf909362b847b2112fd6ec4105ea553ef9a43bafbb5916'
                body = 'owned.fixture.invalid\nsnapshot.fixture.invalid\n'
                checksum = hashlib.sha256(body.encode()).hexdigest()
                with tempfile.TemporaryDirectory(prefix='pihole-tls-fixture-') as directory:
                    cert, key = Path(directory) / 'tls.crt', Path(directory) / 'tls.key'
                    run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                         '-subj', '/CN=snapshot-lists', '-addext', 'subjectAltName=DNS:snapshot-lists',
                         '-keyout', str(key), '-out', str(cert)])
                    certificate = cert.read_text()
                    kube('-n', NS, 'create', '-f', '-', input=json.dumps({'apiVersion': 'v1', 'kind': 'Secret',
                         'metadata': {'name': 'snapshot-tls'}, 'stringData': {'tls.crt': certificate, 'tls.key': key.read_text()}}))
                server = '''import http.server, ssl
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'owned.fixture.invalid\\nsnapshot.fixture.invalid\\n' if self.path == '/list.txt' else b'wrong.fixture.invalid\\n'
        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
server = http.server.HTTPServer(('0.0.0.0', 8443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain('/tls/tls.crt', '/tls/tls.key')
server.socket = context.wrap_socket(server.socket, server_side=True); server.serve_forever()
'''
                kube('-n', NS, 'create', '-f', '-', input=json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': [
                    {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'snapshot-lists', 'labels': {'fixture': 'snapshot-lists'}},
                     'spec': {'automountServiceAccountToken': False, 'containers': [{'name': 'https', 'image': fixture_image,
                         'command': ['python3', '-c', server], 'volumeMounts': [{'name': 'tls', 'mountPath': '/tls', 'readOnly': True}],
                         'readinessProbe': {'tcpSocket': {'port': 8443}}}], 'volumes': [{'name': 'tls', 'secret': {'secretName': 'snapshot-tls'}}]}},
                    {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'snapshot-lists'},
                     'spec': {'selector': {'fixture': 'snapshot-lists'}, 'ports': [{'port': 8443, 'targetPort': 8443}]}}
                ]}))
                kube('-n', NS, 'wait', '--for=condition=Ready', 'pod/snapshot-lists', '--timeout=120s')
                execute('sh', '-ec', 'printf "%s" "$1" | base64 -d > /tmp/snapshot-ca.crt', 'sh', base64.b64encode(certificate.encode()).decode())
                value = policy(); value['requirePinnedLists'] = True
                value['lists']['adlists'] = [{'address': 'https://snapshot-lists:8443/list.txt', 'sha256': checksum}]
                bad = copy.deepcopy(value); bad['lists']['adlists'][0]['address'] = 'https://snapshot-lists:8443/wrong.txt'
                apply(bad, expected=False, ca='/tmp/snapshot-ca.crt')
                require(not api('GET', 'lists?type=block')['lists'], 'checksum failure changed subscriptions')
                api('POST', 'lists?type=block', {'address': 'https://unmanaged.fixture.invalid/list.txt', 'enabled': True})
                apply(value, expected=False, ca='/tmp/snapshot-ca.crt')
                api('DELETE', 'lists/https%3A%2F%2Funmanaged.fixture.invalid%2Flist.txt?type=block')
                apply(value, ca='/tmp/snapshot-ca.crt')
                require(dns('owned.fixture.invalid') == '0.0.0.0', 'verified local snapshot did not filter')
                kube('-n', NS, 'delete', 'pod', 'snapshot-lists', '--wait=true')
                digest = apply(value)  # Provider is unavailable; verified cache must suffice.
                # A periodic no-op must not briefly withdraw a healthy replica
                # or rebuild unchanged immutable content. Watch the marker while
                # the real API/ownership checks run with gravity disabled.
                execute('bash', '-ec', '''
missing=0
env PATH=/tmp/no-gravity:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HELM_POLICY_FILE=/tmp/policy-input.json HELM_POLICY_EXPECTED_REVISION="$1" bash /etc/pihole-helm/import-lists.sh --refresh &
pid=$!
while kill -0 "$pid" 2>/dev/null; do
    if [ "$(cat /run/pihole-helm-policy-ready 2>/dev/null || true)" != "$1" ]; then missing=1; fi
    sleep .01
done
wait "$pid"
test "$missing" = 0
''', 'refresh', digest)
                # An interrupted rebuild still must complete from local verified
                # files with the provider down, even in pinned mode.
                execute('sh', '-ec', 'jq ".gravityDirty=true" /etc/pihole/.helm-policy-state.json > /etc/pihole/.helm-policy-state.json.next; mv /etc/pihole/.helm-policy-state.json.next /etc/pihole/.helm-policy-state.json')
                execute('env', 'HELM_POLICY_FILE=/tmp/policy-input.json', 'HELM_POLICY_EXPECTED_REVISION=' + digest,
                        'bash', '/etc/pihole-helm/import-lists.sh', '--refresh')
                require(dns('owned.fixture.invalid') == '0.0.0.0', 'offline local snapshot refresh lost filtering')
                require('--refresh' in execute('crontab', '-l').stdout, 'stock gravity cron bypasses policy runner')
                apply(policy())
                require(dns('owned.fixture.invalid') == '192.0.2.43', 'last snapshot removal retained stale filtering')
                return {'https_fixture_image': fixture_image, 'sha256': checksum, 'tampered_body_rejected_before_mutation': True,
                        'unmanaged_enabled_feed_rejected': True, 'verified_file_feed_filters': True,
                        'provider_down_reconcile_and_refresh': True, 'scheduled_refresh_uses_runner': True,
                        'unchanged_scheduled_snapshot_check_preserves_readiness': True}
            record('pinned_snapshots_and_offline_refresh', pinned_snapshots)
            def drift_identity():
                apply(desired)
                api('PUT', 'domains/deny/exact/owned.fixture.invalid', {'domain': 'owned.fixture.invalid', 'comment': 'ownership marker removed', 'enabled': False, 'groups': [0]})
                apply(policy(), expected=False)
                require(api('GET', 'domains/deny/exact')['domains'][0]['comment'] == 'ownership marker removed', 'identity conflict was overwritten')
                api('DELETE', 'domains/deny/exact/owned.fixture.invalid')
                apply(policy())
                return {'changed_ownership_marker_refuses_deletion': True}
            record('ownership_identity_conflict', drift_identity)
            def validation():
                bad = policy(); bad['groups'] = [{'name': 'must-not-create'}]; bad['clients'] = [{'id': '192.0.2.9', 'groups': ['missing']}]
                apply(bad, expected=False)
                require(not any(g['name'] == 'must-not-create' for g in api('GET', 'groups')['groups']), 'unknown group changed API')
                apply(policy(), expected=False, revision='0'*64)
                apply(policy())
                return {'all_group_references_prevalidated': True, 'artifact_checksum_enforced': True}
            record('invalid_policy_and_artifact_rejection', validation)
            def write_ahead_recovery():
                apply(desired)
                journal = json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json').stdout)
                for entry in journal['entries']:
                    if entry['kind'] == 'domains/deny/exact':
                        entry['id'] = None
                        entry['date_added'] = None
                encoded = base64.b64encode(json.dumps(journal).encode()).decode()
                execute('sh', '-ec', 'printf "%s" "$1" | base64 -d > /etc/pihole/.helm-policy-state.json.next; sync /etc/pihole/.helm-policy-state.json.next; mv /etc/pihole/.helm-policy-state.json.next /etc/pihole/.helm-policy-state.json', 'sh', encoded)
                require(json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json').stdout) == journal, 'fixture intent journal write failed')
                apply(desired)
                journal = json.loads(execute('cat', '/etc/pihole/.helm-policy-state.json').stdout)
                require(all(e['id'] is not None for e in journal['entries']), 'pending create intent was not recovered')
                apply(policy())
                return {'simulated_post_create_pre_commit_window': True, 'claim_marker_recovers_created_record': True}
            record('write_ahead_create_intent_recovery', write_ahead_recovery)
            # Exercise a failed semantic API operation after a successful earlier
            # mutation; rollback to the old desired policy must remove owned intent.
            def interrupted_repair():
                bad = policy(); bad['groups'] = [{'name': 'partial-group'}]
                bad['lists']['regex_blacklist'] = [{'domain': '['}]
                apply(bad, expected=False)
                apply(policy())
                require(not any(g['name'] == 'partial-group' for g in api('GET', 'groups')['groups']), 'rollback retained partially applied group')
                return {'partial_api_failure_withholds_readiness': True, 'rollback_repairs_journal': True}
            record('partial_apply_rollback_repair', interrupted_repair)
            def retained_rollback():
                values['pihole']['lists'] = {'blacklist': [{'domain': 'owned.fixture.invalid'}]}
                upgrade()
                require(dns('owned.fixture.invalid') == '0.0.0.0', 'new chart revision did not block')
                run(['helm', 'rollback', RELEASE, baseline_revision, '--kube-context', CONTEXT, '-n', NS, '--wait', '--timeout', '3m'])
                artifact = json.loads(kube('-n', NS, 'get', 'configmap', original_artifact, '-o', 'json').stdout)
                require(artifact['immutable'], 'old artifact is not immutable')
                require(dns('owned.fixture.invalid') == '192.0.2.43', 'rollback selected wrong policy')
                return {'original_artifact_retained': True, 'rollback_policy_and_readiness_restored': True}
            record('helm_revision_retention_and_rollback', retained_rollback)
            def functional_tests():
                run(['helm', 'test', RELEASE, '--kube-context', CONTEXT, '-n', NS, '--timeout', '90s'])
                cron = 'policy-audit-pihole-dns-check'
                kube('-n', NS, 'create', 'job', 'synthetic-good', '--from=cronjob/' + cron)
                kube('-n', NS, 'wait', '--for=condition=Complete', 'job/synthetic-good', '--timeout=60s')
                values['validation'] = {'blockedDomain': 'owned.fixture.invalid'}
                values['pihole']['lists'] = {'blacklist': []}
                upgrade()
                result = run(['helm', 'test', RELEASE, '--kube-context', CONTEXT, '-n', NS, '--timeout', '90s'], check=False)
                require(result.returncode != 0, 'Helm test accepted an unblocked domain as blocked')
                kube('-n', NS, 'create', 'job', 'synthetic-bad', '--from=cronjob/' + cron)
                kube('-n', NS, 'wait', '--for=condition=Failed', 'job/synthetic-bad', '--timeout=60s')
                values['validation'] = {}; upgrade()
                run(['helm', 'test', RELEASE, '--kube-context', CONTEXT, '-n', NS, '--timeout', '90s'])
                values['services']['dns'] = {'splitTcpUdp': True, 'tcp': {'type': 'ClusterIP', 'port': 1053}, 'udp': {'type': 'ClusterIP', 'port': 2053}}
                upgrade()
                run(['helm', 'test', RELEASE, '--kube-context', CONTEXT, '-n', NS, '--timeout', '90s'])
                kube('-n', NS, 'create', 'job', 'synthetic-split-ports', '--from=cronjob/' + cron)
                kube('-n', NS, 'wait', '--for=condition=Complete', 'job/synthetic-split-ports', '--timeout=60s')
                return {'udp_tcp_per_replica_and_service': True, 'authenticated_api_and_metrics': True, 'wrong_filtering_answer_rejected': True, 'repeatable_hook_cleanup': True, 'synthetic_job_good_and_bad_answers': True, 'split_services_with_distinct_custom_ports': True}
            record('meaningful_helm_tests_under_network_policy', functional_tests)
    except Exception as exc:
        report['error'] = str(exc)[:2500]
        if created:
            log = kube('-n', NS, 'logs', 'policy-audit-pihole-test-functional', check=False)
            if log.returncode == 0:
                report['functional_test_diagnostic'] = log.stdout[-2500:]
            state = execute('cat', '/etc/pihole/.helm-policy-state.json', check=False)
            if state.returncode == 0:
                report['fixture_journal_diagnostic'] = state.stdout[-5000:]
        print('ERROR ' + report['error'], flush=True)
        save()
    finally:
        if created:
            report['namespace_removed'] = kube('delete', 'namespace', NS, '--timeout=90s', check=False).returncode == 0
        report['completed_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
    if report.get('error') or not report.get('namespace_removed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
