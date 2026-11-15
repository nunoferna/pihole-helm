#!/usr/bin/env python3
"""Promote a validated dependency refresh to the existing isolated homelab staging release.

Requires --promote-staging and a completed matching lifecycle report. Generates
same-disk migration backups without printing credentials; keeps original PVCs.
No production DNS routing, default contexts or unrelated resources are changed.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--promote-staging', action='store_true', required=True)
    parser.add_argument('--fixture-report', type=Path, required=True)
    parser.add_argument('--chart', type=Path, required=True, help='The exact tested chart archive; mutable directories are refused')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/validation/latest-dependency-upgrade.json')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('staging', ROOT / 'scripts/validation/homelab-staging.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    fixture = json.loads(args.fixture_report.read_text())
    m.require(args.chart.is_file(), 'promotion requires the tested chart archive')
    archive_digest = hashlib.sha256(args.chart.read_bytes()).hexdigest()
    m.require(fixture.get('tested_archive_sha256') == archive_digest, 'fixture did not test these exact chart bytes')
    m.require(fixture.get('namespace_removed') and not fixture.get('error') and len(fixture['checks']) >= 13 and all(c['passed'] for c in fixture['checks']), 'matching lifecycle and upgrade fixtures must pass first')
    m.require(fixture['images']['pihole'] == m.IMAGE and fixture['images']['exporter'] == 'ekofr/pihole-exporter:' + m.BASELINE['exporterTag'] + '@' + m.BASELINE['exporterDigest'], 'fixture image baseline differs')
    m.REPORT = args.output
    m.report = {'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'context': m.CONTEXT, 'namespace': m.NS,
                'tested_archive_sha256': archive_digest,
                'target_image': m.IMAGE, 'target_exporter': fixture['images']['exporter'], 'checks': [],
                'scope': 'retained staging dependency upgrade; no production cutover, hard-failure or off-node restoration claim'}
    def ownership(pod):
        journal = json.loads(m.execute(pod, 'cat', '/etc/pihole/.helm-policy-state.json'))
        ordered = sorted(journal['entries'], key=lambda e: (e['kind'], e['key']))
        return hashlib.sha256(json.dumps(ordered, sort_keys=True).encode()).hexdigest()
    before = m.get('statefulset', m.NS)
    m.require(before['spec']['replicas'] == 2 and before['status']['readyReplicas'] == 2, 'two ready staging replicas required')
    m.require(before['spec']['persistentVolumeClaimRetentionPolicy'] == {'whenDeleted': 'Retain', 'whenScaled': 'Retain'}, 'claims must be retained')
    claims = {f'etc-pihole-{m.NS}-{i}': m.get('pvc', f'etc-pihole-{m.NS}-{i}')['metadata']['uid'] for i in range(2)}
    identities = {pod: ownership(pod) for pod in m.PODS}
    m.report['previous_image'] = before['spec']['template']['spec']['containers'][0]['image']
    m.report['controller_uid_before'] = before['metadata']['uid']
    m.report['pvc_uids_before'] = claims
    m.save()
    backup = '/etc/pihole/helm-migration-backup/before-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    try:
        def backup_data():
            for pod in m.PODS:
                m.execute(pod, 'mkdir', '-m', '700', backup)
                m.execute(pod, 'pihole-FTL', 'sqlite3', '/etc/pihole/gravity.db', '.backup ' + backup + '/gravity.db')
                m.execute(pod, 'cp', '/etc/pihole/.helm-policy-state.json', backup + '/ownership.json')
                m.execute(pod, 'cp', '/etc/pihole/pihole.toml', backup + '/pihole.toml')
                m.execute(pod, 'chmod', '600', backup + '/gravity.db', backup + '/ownership.json', backup + '/pihole.toml')
                m.require(m.execute(pod, 'pihole-FTL', 'sqlite3', backup + '/gravity.db', 'PRAGMA integrity_check;').strip() == 'ok', 'backup integrity failed')
            return {'path_on_each_pvc': backup, 'consistent_gravity_backup_integrity': 'ok', 'ownership_and_configuration_retained': True, 'storage_scope': 'same-disk migration safeguard; not disaster recovery'}
        m.record('pre_upgrade_migration_backups', backup_data)
        def upgrade():
            m.run(['helm', 'upgrade', m.NS, str(args.chart), '--kube-context', m.CONTEXT, '-n', m.NS,
                   '-f', str(ROOT / 'deploy/staging/values.yaml'), '-f', str(ROOT / 'deploy/staging/values-homelab.yaml'), '--wait', '--timeout', '4m'], timeout=280)
            current = m.get('statefulset', m.NS)
            m.require(current['metadata']['uid'] == before['metadata']['uid'], 'controller identity changed')
            m.require(current['spec']['template']['spec']['containers'][0]['image'] == m.IMAGE, 'unexpected DNS image')
            exporter = m.get('deployment', m.NS + '-metrics')['spec']['template']['spec']['containers'][0]['image']
            m.require(exporter == fixture['images']['exporter'], 'unexpected exporter image')
            revision = current['spec']['template']['metadata']['annotations']['pihole-helm/policy-revision']
            for name, uid in claims.items():
                m.require(m.get('pvc', name)['metadata']['uid'] == uid, 'PVC identity changed')
            for pod in m.PODS:
                m.require(ownership(pod) == identities[pod], 'ownership identity changed')
                m.require(m.execute(pod, 'pihole-FTL', '--version').strip().lstrip('v') == m.BASELINE['components']['ftl'], 'unexpected running FTL version')
                m.require(m.execute(pod, 'cat', '/run/pihole-helm-policy-ready').strip() == revision, 'policy readiness revision differs')
                m.require(m.execute(pod, 'pihole-FTL', 'sqlite3', '/etc/pihole/gravity.db', 'PRAGMA integrity_check;').strip() == 'ok', 'upgraded database integrity failed')
            document = json.loads(m.get('configmap', current['spec']['template']['metadata']['annotations']['pihole-helm/policy-artifact'])['data']['policy.json'])
            m.require(document['adoptExisting'] is False, 'adoption must remain disabled')
            return {'controller_and_original_pvc_uids_preserved': True, 'ownership_identities_preserved': True, 'running_ftl': m.BASELINE['components']['ftl'], 'database_integrity': 'ok', 'adoption_disabled': True, 'policy_revision': revision}
        m.record('normal_dependency_upgrade', upgrade)
        def helm_test():
            m.run(['helm', 'test', m.NS, '--kube-context', m.CONTEXT, '-n', m.NS, '--timeout', '120s'])
            return {'per_replica_and_service_udp_tcp_filtering_upstream_authentication_metrics': 'passed'}
        m.record('updated_image_functional_helm_test', helm_test)
        def external():
            for node in ('192.168.1.73', '192.168.1.77'):
                for tcp in (False, True):
                    for name, expected in (('staging-health.invalid', '192.0.2.42'), ('staging-blocked.invalid', '0.0.0.0')):
                        m.require(m.answer(node, 32052, name, tcp, True) == expected, 'external answer differs')
            return {'workstation_nodeport_udp_tcp_local_and_filtering': 'passed'}
        m.record('external_dns_after_upgrade', external)
        def scheduled():
            cutoff = datetime.datetime.now(datetime.timezone.utc)
            deadline = time.monotonic() + 95
            while time.monotonic() < deadline:
                job = m.get('cronjob', m.NS + '-dns-check')
                stamp = job.get('status', {}).get('lastSuccessfulTime')
                if stamp and datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00')) >= cutoff:
                    m.require(job['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['image'] == m.IMAGE, 'synthetic job uses unexpected image')
                    return {'last_successful_time': stamp, 'schedule': job['spec']['schedule'], 'image': m.IMAGE}
                time.sleep(3)
            raise AssertionError('no fresh scheduled DNS success')
        m.record('updated_scheduled_dns_success', scheduled)
        m.record('updated_exporter_scrape_and_alert_rules', m.prometheus)
    except Exception as exc:
        m.report['error'] = str(exc)[:2500]
        print('ERROR ' + m.report['error'], flush=True)
        raise
    finally:
        m.report['staging_retained'] = True
        m.report['completed_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        m.save()


if __name__ == '__main__':
    main()
