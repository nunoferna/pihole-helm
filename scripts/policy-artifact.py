#!/usr/bin/env python3
"""Generate an explicit active-and-retained immutable policy artifact set for GitOps.

Requires Helm, jq and PyYAML. Reads values, never accesses a cluster or writes Secrets.
Retained artifacts must belong to the same namespace/release; garbage collection
is deliberately explicit, after live pods and rollback revisions stop using them.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def validate(document, namespace, release, owner=None):
    if not isinstance(document, dict) or document.get('kind') != 'ConfigMap' or document.get('immutable') is not True:
        raise ValueError('Only immutable policy ConfigMaps can be retained')
    data = document['data']
    for key in ('policy.json', 'import-lists.sh', 'policy.jq'):
        if not isinstance(data.get(key), str):
            raise ValueError('Policy artifact is missing runner/data content')
    if sum(len(value.encode()) for value in data.values()) > 900_000:
        raise ValueError('Policy artifact exceeds the supported size budget; keep list bodies outside ConfigMaps')
    policy = json.loads(data['policy.json'])
    if policy.get('owner') != (owner or namespace + '/' + release) or document['metadata'].get('namespace', namespace) != namespace:
        raise ValueError('Retained policy artifact belongs to another release/namespace')
    revision = hashlib.sha256(data['policy.json'].encode()).hexdigest()
    if revision != document['metadata']['annotations']['pihole-helm/policy-revision']:
        raise ValueError('Retained policy artifact checksum differs')
    content = ''.join(data[key] for key in ('policy.json', 'import-lists.sh', 'policy.jq'))
    if not document['metadata']['name'].endswith('-policy-' + hashlib.sha256(content.encode()).hexdigest()[:20]):
        raise ValueError('Retained artifact name does not match its complete content')
    document['metadata']['namespace'] = namespace
    document['metadata']['labels']['app.kubernetes.io/managed-by'] = 'pihole-policy-artifacts'
    return revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('-f', '--values', action='append', default=[])
    parser.add_argument('--retain', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference-output', type=Path, required=True, help='Values overlay selecting the active artifact and checksum')
    args = parser.parse_args()
    if args.output.resolve() == args.reference_output.resolve():
        raise ValueError('Artifact and reference outputs must be different files')
    command = ['helm', 'template', args.release, str(ROOT / 'charts/pihole'), '--namespace', args.namespace,
               '--show-only', 'templates/configmap-lists.yaml']
    for path in args.values:
        command += ['-f', path]
    command += ['--set', 'policy.existingConfigMap=', '--set', 'policy.revision=']
    rendered = subprocess.run(command, capture_output=True, text=True)
    if rendered.returncode:
        raise RuntimeError(rendered.stderr)
    active = [d for d in yaml.safe_load_all(rendered.stdout) if d]
    if len(active) != 1:
        raise ValueError('Policy management must be enabled to generate an artifact')
    owner = json.loads(active[0]['data']['policy.json'])['owner']
    revision = validate(active[0], args.namespace, args.release, owner)
    normalized = subprocess.run(['jq', '-ce', '-f', str(ROOT / 'charts/pihole/files/policy.jq')],
                                input=active[0]['data']['policy.json'], capture_output=True, text=True)
    if normalized.returncode:
        raise ValueError('Active policy failed structural validation')
    documents = {}
    for path in args.retain:
        for document in yaml.safe_load_all(path.read_text()):
            if document:
                validate(document, args.namespace, args.release, owner)
                name = document['metadata']['name']
                if name in documents and documents[name]['data'] != document['data']:
                    raise ValueError('Conflicting retained artifact content')
                documents[name] = document
    name = active[0]['metadata']['name']
    if name in documents and documents[name]['data'] != active[0]['data']:
        raise ValueError('Active artifact conflicts with retained content')
    documents[name] = active[0]
    args.output.write_text(yaml.safe_dump_all([documents[n] for n in sorted(documents)], sort_keys=False))
    args.reference_output.write_text(yaml.safe_dump({'policy': {'existingConfigMap': name, 'revision': revision}}, sort_keys=False))
    print(json.dumps({'active': name, 'revision': revision, 'retained_count': len(documents)}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
