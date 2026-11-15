#!/usr/bin/env python3
"""Encrypted per-replica backup and restore into an EMPTY, separately provisioned PVC.

Requires kubectl, age and access to the selected namespace. Always specify context.
No plaintext archive is written to workstation disk. The source pod stays running.
Restore does not install a chart or create credentials; retain these independently.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / "charts/pihole/files/compatibility.json").read_text())
IMAGE = "pihole/pihole:" + BASELINE["dockerTag"] + "@" + BASELINE["digest"]

BACKUP = r'''set -euo pipefail
exec 9>/etc/pihole/.helm-policy.lock
for attempt in {1..10}; do
    if flock -n 9; then break; fi
    if [ "$attempt" = 10 ]; then echo 'Policy operation still holds the backup lock' >&2; exit 1; fi
    sleep 1
done
test -f /etc/pihole/.helm-policy-state.json
test -s /run/pihole-helm-policy-ready
jq -e --arg revision "$(cat /run/pihole-helm-policy-ready)" '.revision==$revision and .gravityDirty==false' /etc/pihole/.helm-policy-state.json >/dev/null
stage=$(mktemp -d /etc/pihole/.helm-backup.XXXXXX)
trap 'rm -rf "$stage"' EXIT
# SQLite backup API includes committed WAL data; never copy a live database.
pihole-FTL sqlite3 /etc/pihole/gravity.db ".timeout 10000" ".backup '$stage/gravity.db'"
test "$(pihole-FTL sqlite3 "$stage/gravity.db" 'PRAGMA integrity_check;')" = ok
for name in pihole.toml .helm-policy-state.json adlists.list helm-snapshots tls.pem hosts; do
    if [ -e "/etc/pihole/$name" ]; then cp -a "/etc/pihole/$name" "$stage/$name"; fi
done
test -z "$(find "$stage" ! -type f ! -type d -print -quit)" || { echo 'Backup contains unsupported links or special files' >&2; exit 1; }
printf '%s' "$1" > "$stage/.helm-backup.json"
tar -C "$stage" -cf - .
'''

RESTORE = r'''set -euo pipefail
test -z "$(find /restore -mindepth 1 -maxdepth 1 ! -name lost+found -print -quit)" || { echo 'Restore PVC must be empty' >&2; exit 1; }
touch /restore/.restore-incomplete
tar -C /restore -xf -
jq -e --arg image "$1" '.formatVersion==1 and .image==$image' /restore/.helm-backup.json >/dev/null
test "$(pihole-FTL sqlite3 /restore/gravity.db 'PRAGMA integrity_check;')" = ok
jq -e '.formatVersion==1 and (.owner|type=="string") and (.entries|type=="array")' /restore/.helm-policy-state.json >/dev/null
chmod 600 /restore/.helm-policy-state.json
sync
rm /restore/.restore-incomplete
'''


def command(args, *parts):
    return ["kubectl", "--context", args.context, "--namespace", args.namespace, *parts]


def run(args, *parts, input=None):
    result = subprocess.run(command(args, *parts), input=input, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-1500:])
    return result.stdout


def pipe(first, second, output=None):
    # No decrypted archive or configuration is written to workstation storage.
    with tempfile.TemporaryFile() as errors_a, tempfile.TemporaryFile() as errors_b:
        source = subprocess.Popen(first, stdout=subprocess.PIPE, stderr=errors_a)
        try:
            target = subprocess.Popen(second, stdin=source.stdout, stdout=output or subprocess.DEVNULL, stderr=errors_b)
            source.stdout.close()
            try:
                target.wait(timeout=300)
                source.wait(timeout=30)
            except BaseException:
                target.kill(); target.wait()
                raise
        except BaseException:
            source.kill(); source.wait()
            raise
        if source.returncode or target.returncode:
            errors_a.seek(0); errors_b.seek(0)
            raise RuntimeError((errors_a.read() + errors_b.read()).decode(errors="replace")[-1500:])


def backup(args):
    pod = json.loads(run(args, "get", "pod", args.pod, "-o", "json"))
    container = next(c for c in pod["spec"]["containers"] if c["name"] == "pihole")
    metadata = {"formatVersion": 1, "createdUTC": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "namespace": args.namespace, "pod": args.pod, "podUID": pod["metadata"]["uid"],
                "image": container["image"], "historyIncluded": False}
    # Exclusive creation protects a previously verified recovery point.
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            pipe(command(args, "exec", args.pod, "-c", "pihole", "--", "bash", "-ec", BACKUP,
                         "backup", json.dumps(metadata)), ["age", "-r", args.recipient], output)
            output.flush(); os.fsync(output.fileno())
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise
    return {"backup": str(args.output), "sha256": file_digest(args.output), "encrypted": True,
            "image": container["image"], "queryHistoryIncluded": False}


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def restore(args):
    if not re.fullmatch('[a-f0-9]{64}', args.expected_sha256) or file_digest(args.input) != args.expected_sha256:
        raise ValueError('Encrypted backup does not match the independently retained expected SHA-256')
    claim = json.loads(run(args, "get", "pvc", args.pvc, "-o", "json"))
    if claim.get("status", {}).get("phase") not in ("Pending", "Bound"):
        raise ValueError("Restore claim must be Pending or Bound")
    pods = json.loads(run(args, "get", "pods", "-o", "json"))["items"]
    if any(v.get("persistentVolumeClaim", {}).get("claimName") == args.pvc for p in pods for v in p["spec"].get("volumes", [])):
        raise ValueError("Restore PVC is referenced by a pod; restore into a separate empty claim")
    name = "pihole-restore-" + uuid.uuid4().hex[:12]
    helper = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name}, "spec": {
        "restartPolicy": "Never", "automountServiceAccountToken": False,
        "containers": [{"name": "restore", "image": args.image, "command": ["sleep", "600"],
                        "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False,
                                            "capabilities": {"drop": ["ALL"], "add": ["CHOWN", "FOWNER", "DAC_OVERRIDE"]}},
                        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}},
                        "volumeMounts": [{"name": "data", "mountPath": "/restore"}]}],
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": args.pvc}}]}}
    run(args, "create", "-f", "-", input=json.dumps(helper).encode())
    try:
        run(args, "wait", "--for=condition=Ready", "pod/" + name, "--timeout=120s")
        pipe(["age", "-d", "-i", str(args.identity), str(args.input)],
             command(args, "exec", "-i", name, "--", "bash", "-ec", RESTORE, "restore", args.image))
        journal = json.loads(run(args, "exec", name, "--", "cat", "/restore/.helm-policy-state.json"))
        return {"restoredPVC": args.pvc, "integrity": "ok", "policyOwner": journal["owner"],
                "nextStep": "Install the same image with policy.owner set to the original identity, original desired policy and independently retained credentials"}
    finally:
        run(args, "delete", "pod", name, "--wait=true", "--timeout=60s")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("backup", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("--context", required=True)
        sub.add_argument("--namespace", required=True)
        if name == "backup":
            sub.add_argument("--pod", required=True)
            sub.add_argument("--recipient", required=True)
            sub.add_argument("--output", type=Path, required=True)
        else:
            sub.add_argument("--pvc", required=True)
            sub.add_argument("--identity", type=Path, required=True)
            sub.add_argument("--input", type=Path, required=True)
            sub.add_argument("--expected-sha256", required=True, help='Ciphertext SHA-256 from trusted backup metadata retained separately')
            sub.add_argument("--image", default=IMAGE)
    args = parser.parse_args()
    try:
        print(json.dumps(backup(args) if args.operation == "backup" else restore(args)))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Recovery operation failed: {error}\n")


if __name__ == "__main__":
    main()
