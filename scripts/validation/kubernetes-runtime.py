#!/usr/bin/env python3
"""Smoke the chart and proposed lifecycle in the dedicated pihole-audit-k8s cluster.

Requires PyYAML, Helm, kubectl and the pinned image already loaded into kind.
Never uses the default kubeconfig/context. Deletes only its own test namespace.
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

# amd64 manifest of the verified multi-platform index; explicit platform import
# avoids requiring every architecture's blobs in Docker's local image store.
IMAGE = "pihole/pihole:2026.10.0@sha256:d8d848307ffe7f8c4b42be423b33295d6a45282215eb5a96fe25e05fa39b1abc"
NS = "pihole-audit-validation"


def main():
    global NS
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kubeconfig", default="/tmp/pihole-audit-k8s.kubeconfig")
    p.add_argument("--output", default="docs/validation/kubernetes-runtime.json")
    p.add_argument("--only-lifecycle", action="store_true")
    p.add_argument("--namespace", choices=[NS, "pihole-audit-lifecycle"], default=NS)
    args = p.parse_args()
    NS = args.namespace
    command = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", "kind-pihole-audit-k8s"]
    report = {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "scope": "one-node kind smoke; no node-failure or load-balancer HA claim", "image": IMAGE,
              "fixture_overrides": ["explicit local DNS record", "empty initial adlists", "static TEST-NET hostAliases for gravity bootstrap check", "ClusterIP DNS", "no configured list/domain rules", "512Mi memory limit"],
              "checks": []}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(24)
    created = False

    def run(cmd, input=None, check=True, timeout=120):
        r = subprocess.run(cmd, input=input, text=True, capture_output=True, timeout=timeout)
        if check and r.returncode:
            raise RuntimeError((r.stderr or r.stdout)[:1600].replace(password, "[REDACTED]"))
        return r

    def kubectl(*params, input=None, check=True, timeout=120):
        return run(command + list(params), input, check, timeout)

    def get(kind, name=None):
        return json.loads(kubectl("-n", NS, "get", kind, *([name] if name else []), "-o", "json").stdout)

    def apply(objects):
        kubectl("apply", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))

    def save():
        output.write_text(json.dumps(report, indent=2) + "\n")

    def record(name, fn):
        start = time.monotonic()
        try:
            r = {"check": name, "passed": True, "details": fn()}
        except Exception as exc:
            r = {"check": name, "passed": False, "error": str(exc).replace(password, "[REDACTED]")[:1600]}
        r["seconds"] = round(time.monotonic() - start, 2)
        report["checks"].append(r)
        save()
        print(json.dumps(r), flush=True)
        return r

    def require(value, message):
        if not value:
            raise AssertionError(message)

    aliases = [{"ip": "192.0.2.1", "hostnames": ["raw.githubusercontent.com", "github.com"]}]
    try:
        nodes = json.loads(kubectl("get", "nodes", "-o", "json").stdout)["items"]
        require(len(nodes) == 1 and nodes[0]["metadata"]["name"] == "pihole-audit-k8s-control-plane", "wrong cluster")
        report["kubernetes_version"] = nodes[0]["status"]["nodeInfo"]["kubeletVersion"]
        require(kubectl("get", "namespace", NS, check=False).returncode != 0, "test namespace already exists; refusing to overwrite")
        kubectl("create", "namespace", NS)
        created = True
        if not args.only_lifecycle:
            values = {'replicas': 2, 'image': {'tag': IMAGE.split(':', 1)[1], 'pullPolicy': 'IfNotPresent'}, 'pihole': {'web': {'password': password}, 'lists': {'adlists': [], 'whitelist': [], 'blacklist': [], 'regex_whitelist': [], 'regex_blacklist': []}, 'groups': [], 'clients': [], 'ftl': {'dns': {'upstreams': ['192.0.2.53'], 'hosts': ['192.0.2.123 health.audit.test'], 'listeningMode': 'ALL'}, 'ntp': {'ipv4': {'active': False}, 'ipv6': {'active': False}, 'sync': {'active': False}}, 'misc': {'nice': 0}}}, 'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}}, 'podDisruptionBudget': {'enabled': True, 'minAvailable': 2}, 'extraInitContainers': [{'name': 'seed-empty-adlists', 'image': IMAGE, 'imagePullPolicy': 'IfNotPresent', 'command': ['/bin/sh', '-c', ': > /etc/pihole/adlists.list'], 'volumeMounts': [{'name': 'etc-pihole', 'mountPath': '/etc/pihole'}]}], 'services': {'dns': {'type': 'ClusterIP'}}}
            with tempfile.TemporaryDirectory(prefix="pihole-audit-k8s-values-") as temp:
                valuefile = Path(temp) / "values.yaml"
                valuefile.write_text(yaml.safe_dump(values))
                objects = [o for o in yaml.safe_load_all(run(["helm", "template", "audit", "charts/pihole", "--namespace", NS, "--skip-tests", "-f", str(valuefile)]).stdout) if o]
            for obj in objects:
                obj["metadata"]["namespace"] = NS
                if obj["kind"] == "StatefulSet":
                    obj["spec"]["template"]["spec"]["hostAliases"] = aliases
            apply(objects)

            def rollout():
                kubectl("-n", NS, "rollout", "status", "statefulset/audit-pihole", "--timeout=150s", timeout=170)
                answers = {}
                for pod in ("audit-pihole-0", "audit-pihole-1"):
                    answers[pod] = {}
                    for protocol in ("udp", "tcp"):
                        answer = kubectl("-n", NS, "exec", pod, "-c", "pihole", "--", "dig", "+short", "+time=2", "+retry=0", *(["+tcp"] if protocol == "tcp" else []), "@127.0.0.1", "health.audit.test").stdout.strip()
                        require(answer == "192.0.2.123", "unexpected local DNS result")
                        answers[pod][protocol] = answer
                answers["unprivileged_port_start"] = kubectl("-n", NS, "exec", "audit-pihole-0", "-c", "pihole", "--", "cat", "/proc/sys/net/ipv4/ip_unprivileged_port_start").stdout.strip()
                return answers
            record("current_chart_two_replica_startup", rollout)

            # Reproduce selector leakage with unrelated Ready pods carrying exactly
            # the component/application labels the chart assigns to metrics/tests.
            for name, component in (("audit-metrics-fixture", "metrics"), ("audit-test-fixture", None)):
                labels = {"app.kubernetes.io/name": "pihole", "app.kubernetes.io/instance": "audit"}
                if component:
                    labels["app.kubernetes.io/component"] = component
                apply([{"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": NS, "labels": labels},
                        "spec": {"containers": [{"name": "fixture", "image": IMAGE, "imagePullPolicy": "IfNotPresent", "command": ["/bin/sh", "-c", "sleep 300"], "resources": {"limits": {"memory": "64Mi", "cpu": "100m"}}}]}}])
            kubectl("-n", NS, "wait", "--for=condition=Ready", "pod/audit-metrics-fixture", "pod/audit-test-fixture", "--timeout=45s")
            time.sleep(3)

            def selectors():
                slices = get("endpointslices")["items"]
                targets = sorted({ep.get("targetRef", {}).get("name") for s in slices if s["metadata"]["labels"].get("kubernetes.io/service-name") == "audit-pihole-headless" for ep in s.get("endpoints", []) if ep.get("conditions", {}).get("ready")})
                pdb = get("pdb", "audit-pihole")["status"]
                require("audit-metrics-fixture" in targets and "audit-test-fixture" in targets, "selector leak not reproduced")
                require(pdb.get("currentHealthy", 0) >= 4, "PDB did not count unrelated pods")
                return {"finding": "confirmed defect", "headless_ready_targets": targets, "pdb_status": pdb}
            record("reproduce_headless_and_pdb_selector_leak", selectors)

        hook_command = """set -eu
rm -f /run/audit-hook-ready
deadline=$(( $(date +%s) + 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if [ -s /etc/pihole/cli_pw ]; then
    response=$(jq -nc --rawfile password /etc/pihole/cli_pw '{password:($password|rtrimstr("\\n"))}' | curl -fsS --max-time 2 -H 'Content-Type: application/json' --data-binary @- http://127.0.0.1:8080/api/auth) || response=''
    if printf '%s' "$response" | jq -e '.session.valid == true' >/dev/null 2>&1; then
      sid=$(printf '%s' "$response" | jq -r .session.sid)
      curl -fsS --max-time 2 -X DELETE -H "sid: $sid" http://127.0.0.1:8080/api/auth >/dev/null || true
      date -u +%FT%TZ > /run/audit-hook-ready
      exit 0
    fi
  fi
  sleep 1
done
exit 1
"""
        lifecycle = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "audit-lifecycle", "namespace": NS}, "spec": {
            "hostAliases": aliases, "volumes": [{"name": "data", "emptyDir": {}}],
            "initContainers": [{"name": "seed", "image": IMAGE, "imagePullPolicy": "IfNotPresent", "command": ["/bin/sh", "-c", ": > /etc/pihole/adlists.list"], "volumeMounts": [{"name": "data", "mountPath": "/etc/pihole"}]}],
            "containers": [{"name": "pihole", "image": IMAGE, "imagePullPolicy": "IfNotPresent", "securityContext": {"allowPrivilegeEscalation": False},
                "env": [{"name": k, "value": v} for k, v in {
                    "FTLCONF_webserver_api_password": password, "FTLCONF_webserver_port": "8080", "FTLCONF_dns_upstreams": "192.0.2.53", "FTLCONF_dns_hosts": "192.0.2.123 health.audit.test", "FTLCONF_dns_listeningMode": "ALL", "FTLCONF_ntp_ipv4_active": "false", "FTLCONF_ntp_ipv6_active": "false", "FTLCONF_ntp_sync_active": "false", "FTLCONF_misc_nice": "0"}.items()],
                "volumeMounts": [{"name": "data", "mountPath": "/etc/pihole"}], "resources": {"limits": {"memory": "512Mi", "cpu": "1"}},
                "lifecycle": {"postStart": {"exec": {"command": ["/bin/sh", "-c", hook_command]}}},
                "startupProbe": {"exec": {"command": ["/bin/sh", "-c", "test -s /run/audit-hook-ready"]}, "periodSeconds": 2, "failureThreshold": 45},
                "readinessProbe": {"exec": {"command": ["/bin/sh", "-c", "test -s /run/audit-hook-ready && dig +short +time=1 +retry=0 @127.0.0.1 health.audit.test | grep -qx 192.0.2.123"]}, "periodSeconds": 2}
            }]}}
        apply([lifecycle])

        def hook():
            kubectl("-n", NS, "wait", "--for=condition=Ready", "pod/audit-lifecycle", "--timeout=100s", timeout=120)
            pod = get("pod", "audit-lifecycle")
            marker = kubectl("-n", NS, "exec", "audit-lifecycle", "-c", "pihole", "--", "cat", "/run/audit-hook-ready").stdout.strip()
            return {"authenticated_loopback_api_hook_completed_at": marker, "ready": True, "restart_count": pod["status"]["containerStatuses"][0]["restartCount"], "scope": "authenticated API and probe ordering only; full policy runner not implemented"}
        record("bounded_poststart_loopback_api_and_probes", hook)
    except Exception as exc:
        report["fatal_error"] = str(exc).replace(password, "[REDACTED]")[:1800]
        print("ERROR " + report["fatal_error"], flush=True)
    finally:
        if created:
            pods = kubectl("-n", NS, "get", "pods", "-o", "json", check=False)
            if pods.returncode == 0:
                report["final_pod_states"] = [{"name": pod["metadata"]["name"], "phase": pod["status"].get("phase"), "containers": [{"name": c["name"], "ready": c["ready"], "restarts": c["restartCount"], "state": c["state"]} for c in pod["status"].get("containerStatuses", [])]} for pod in json.loads(pods.stdout)["items"]]
            deleted = kubectl("delete", "namespace", NS, "--wait=true", "--timeout=90s", check=False, timeout=100)
            report["namespace_cleanup_succeeded"] = deleted.returncode == 0
        report["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
    if report.get("fatal_error") or not report.get("namespace_cleanup_succeeded") or any(not item["passed"] for item in report["checks"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
