#!/usr/bin/env python3
"""Exercise v2 in the dedicated one-node pihole-audit-v2 kind cluster.

Requires Helm, kubectl and PyYAML. Uses a separate kubeconfig and its own namespace.
The caller creates/deletes the dedicated cluster. No default Kubernetes context is used.
"""
import argparse
import datetime
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = "/tmp/pihole-audit-v2.kubeconfig"
CONTEXT = "kind-pihole-audit-v2"
NS = "pihole-v2-validation"
IMAGE = "pihole/pihole:2026.10.0@sha256:d8d848307ffe7f8c4b42be423b33295d6a45282215eb5a96fe25e05fa39b1abc"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-on-failure", action="store_true")
    args = parser.parse_args()
    report = {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "chart_version": "2.0.0-alpha.1", "scope": "single-node amd64 v2 integration; no external HA certification", "checks": []}
    output = ROOT / "docs/validation/v2-runtime.json"
    password = secrets.token_urlsafe(24)
    created = False

    def run(command, input=None, check=True, timeout=210):
        r = subprocess.run(command, text=True, input=input, capture_output=True, timeout=timeout)
        if check and r.returncode:
            raise RuntimeError((r.stderr or r.stdout)[-2000:].replace(password, "[REDACTED]"))
        return r

    def kube(*args, input=None, check=True):
        return run(["kubectl", "--kubeconfig", CONFIG, "--context", CONTEXT, *args], input, check)

    def get(kind, name):
        return json.loads(kube("-n", NS, "get", kind, name, "-o", "json").stdout)

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")

    def record(name, function):
        started = time.monotonic()
        details = function()
        report["checks"].append({"check": name, "passed": True, "seconds": round(time.monotonic() - started, 2), "details": details})
        save()
        print(json.dumps(report["checks"][-1]), flush=True)
        return details

    def apply(objects):
        kube("apply", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))

    def exec_in(pod, *args, input=None):
        return kube("-n", NS, "exec", "-i", pod, "--", *args, input=input).stdout

    try:
        node = json.loads(kube("get", "nodes", "-o", "json").stdout)["items"]
        require(len(node) == 1 and node[0]["metadata"]["name"] == "pihole-audit-v2-control-plane", "wrong cluster")
        require(kube("get", "namespace", NS, check=False).returncode != 0, "test namespace already exists")
        kube("create", "namespace", NS)
        created = True
        report["kubernetes_version"] = node[0]["status"]["nodeInfo"]["kubeletVersion"]
        comment = "literal apostrophe ' quote \" dollar $(false) and `false`"
        values = {'replicas': 1, 'image': {'digest': IMAGE.split('@', 1)[1]}, 'listeners': {'https': 0}, 'pihole': {'web': {'password': password}, 'lists': {'blacklist': [{'domain': 'blocked.audit.test', 'comment': comment}]}, 'groups': [{'name': 'audit-group', 'enabled': True}], 'clients': [{'id': '192.0.2.22', 'groups': ['audit-group'], 'comment': comment}], 'ftl': {'dns': {'upstreams': ['192.0.2.53'], 'hosts': ['192.0.2.123 health.audit.test', '192.0.2.11 blocked.audit.test']}}}, 'networking': {'hostAliases': [{'ip': '192.0.2.1', 'hostnames': ['raw.githubusercontent.com', 'github.com']}]}, 'podSecurityContext': {'sysctls': [{'name': 'net.ipv4.ip_unprivileged_port_start', 'value': '1024'}]}, 'metrics': {'enabled': True}, 'probes': {'dns': {'name': 'health.audit.test', 'expectedAddress': '192.0.2.123'}}, 'resources': {'limits': {'cpu': '1', 'memory': '512Mi'}}, 'podDisruptionBudget': {'enabled': True, 'minAvailable': 1}, 'extraInitContainers': [{'name': 'seed', 'image': IMAGE, 'command': ['/bin/sh', '-c', ': > /etc/pihole/adlists.list'], 'volumeMounts': [{'name': 'etc-pihole', 'mountPath': '/etc/pihole'}]}], 'services': {'dns': {'type': 'ClusterIP'}}}
        with tempfile.TemporaryDirectory(prefix="pihole-v2-values-") as tmp:
            path = Path(tmp) / "values.yaml"

            def upgrade():
                path.write_text(yaml.safe_dump(values))
                r = run(["helm", "upgrade", "--install", "audit", str(ROOT / "charts/pihole"), "--namespace", NS,
                         "--kubeconfig", CONFIG, "--kube-context", CONTEXT, "-f", str(path), "--wait", "--timeout", "180s"])
                require(password not in r.stdout, "Helm output exposed fixture credential")

            record("install_one_replica", lambda: (upgrade() or {"ready": get("statefulset", "audit-pihole")["status"]["readyReplicas"]}))
            uid = get("statefulset", "audit-pihole")["metadata"]["uid"]
            apply([{"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "client", "namespace": NS}, "spec": {"automountServiceAccountToken": False, "containers": [{"name": "client", "image": IMAGE, "command": ["/bin/sh", "-c", "sleep 900"]}]}}])
            kube("-n", NS, "wait", "--for=condition=Ready", "pod/client", "--timeout=45s")

            def query_service():
                answers = {}
                for protocol in ("udp", "tcp"):
                    answers[protocol] = {}
                    for name, expected in (("health.audit.test", "192.0.2.123"), ("blocked.audit.test", "0.0.0.0")):
                        answer = exec_in("client", "dig", "+short", "+time=2", "+retry=0", *(["+tcp"] if protocol == "tcp" else []), "@audit-pihole-dns", name).strip()
                        require(answer == expected, "Service resolution/filtering mismatch: " + name)
                        answers[protocol][name] = answer
                answers["unprivileged_port_start"] = exec_in("audit-pihole-0", "cat", "/proc/sys/net/ipv4/ip_unprivileged_port_start").strip()
                require(answers["unprivileged_port_start"] == "1024", "security fixture not applied")
                return answers
            record("service_53_to_5353_and_api_policy", query_service)

            values["replicas"] = 2
            record("scale_one_to_two", lambda: (upgrade() or {"same_controller_uid": get("statefulset", "audit-pihole")["metadata"]["uid"] == uid, "ready": get("statefulset", "audit-pihole")["status"]["readyReplicas"]}))
            require(get("statefulset", "audit-pihole")["metadata"]["uid"] == uid, "scaling replaced controller")
            for name, component in (("unrelated-metrics", "metrics"), ("unrelated-test", "test")):
                apply([{"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": NS, "labels": {"app.kubernetes.io/name": "pihole", "app.kubernetes.io/instance": "audit", "app.kubernetes.io/component": component}}, "spec": {"containers": [{"name": "fixture", "image": IMAGE, "command": ["/bin/sh", "-c", "sleep 600"]}]}}])
            kube("-n", NS, "wait", "--for=condition=Ready", "pod/unrelated-metrics", "pod/unrelated-test", "--timeout=45s")
            time.sleep(3)

            def selection():
                slices = json.loads(kube("-n", NS, "get", "endpointslices", "-l", "kubernetes.io/service-name=audit-pihole-headless", "-o", "json").stdout)["items"]
                targets = sorted({e["targetRef"]["name"] for s in slices for e in s.get("endpoints", [])})
                require(targets == ["audit-pihole-0", "audit-pihole-1"], "headless selection leaked")
                status = get("pdb", "audit-pihole")["status"]
                require(status["currentHealthy"] == 2 and status["expectedPods"] == 2, "PDB counted unrelated pods")
                return {"headless_targets": targets, "pdb_healthy": status["currentHealthy"], "pdb_expected": status["expectedPods"]}
            record("isolated_headless_and_pdb", selection)

            def metrics():
                text = exec_in("client", "curl", "-fsS", "--max-time", "15", "http://audit-pihole-metrics:9617/metrics")
                statuses = {}
                for name in ("audit-pihole-0", "audit-pihole-1"):
                    samples = [line for line in text.splitlines() if line.startswith("pihole_status{") and name + "." in line]
                    require(len(samples) == 1 and float(samples[0].rsplit(" ", 1)[1]) == 1, "missing active blocking status for " + name)
                    statuses[name] = 1
                return {"authenticated_both_targets": True, "blocking_status": statuses}
            record("automatic_exporter_auth_and_port_mapping", metrics)

            password = secrets.token_urlsafe(24) + ",fixture-comma"
            values["pihole"]["web"]["password"] = password
            values["credentialsRevision"] = "rotation-2"
            record("rotate_credentials", lambda: (upgrade() or {"dns_ready": get("statefulset", "audit-pihole")["status"]["readyReplicas"], "exporter_ready": get("deployment", "audit-pihole-metrics")["status"]["readyReplicas"]}))
            record("metrics_after_rotation", metrics)
            record("dns_after_rotation", query_service)
    except Exception as exc:
        report["error"] = str(exc).replace(password, "[REDACTED]")[:2200]
        print("ERROR " + report["error"], flush=True)
        if created:
            pods = kube("-n", NS, "get", "pods", "-o", "json", check=False)
            if pods.returncode == 0:
                report["pod_diagnostics"] = []
                for pod in json.loads(pods.stdout)["items"]:
                    name = pod["metadata"]["name"]
                    diagnostic = {"name": name, "status": pod["status"]}
                    if name.startswith("audit-pihole-") and "metrics" not in name:
                        logs = kube("-n", NS, "logs", name, "-c", "pihole", "--tail=35", check=False)
                        diagnostic["logs"] = (logs.stdout + logs.stderr).replace(password, "[REDACTED]")
                    report["pod_diagnostics"].append(diagnostic)
            report["events"] = kube("-n", NS, "get", "events", "--sort-by=.lastTimestamp", check=False).stdout[-8000:]
    finally:
        if created and not (args.keep_on_failure and report.get("error")):
            report["namespace_cleanup_succeeded"] = kube("delete", "namespace", NS, "--timeout=90s", check=False).returncode == 0
        report["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
    if report.get("error") or not report.get("namespace_cleanup_succeeded"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
