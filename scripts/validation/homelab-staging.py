#!/usr/bin/env python3
"""Validate the retained pihole-staging release on admin@homelab.

Replaces each staging DNS pod sequentially. Optional v1-to-v2 Secret rotation
requires --rotate-credentials and fails its precondition before any pod changes.
Never changes the default context, production DNS or unrelated workloads.
Credentials travel through captured subprocess input/output only.
"""
import argparse
import base64
import datetime
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
NS = "pihole-staging"
CONTEXT = "admin@homelab"
CLIENT = "staging-validation"
BASELINE = json.loads((ROOT / "charts/pihole/files/compatibility.json").read_text())
IMAGE = "pihole/pihole:" + BASELINE["dockerTag"] + "@" + BASELINE["digest"]
PODS = ["pihole-staging-0", "pihole-staging-1"]
REPORT = ROOT / "docs/validation/homelab-staging.json"
report = {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "context": CONTEXT, "namespace": NS, "checks": [],
          "scope": "two homelab nodes; NodePort; directory-backed retained local PVs; no node power-off or production cutover"}


def run(args, input=None, timeout=210, check=True):
    r = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError("Command failed: " + args[0] + ": " + r.stderr[-1800:])
    return r


def kube(*args, input=None, check=True, timeout=210):
    return run(["kubectl", "--context", CONTEXT, *args], input, timeout, check)


def get(kind, name, namespace=NS):
    return json.loads(kube("-n", namespace, "get", kind, name, "-o", "json").stdout)


def execute(pod, *args, input=None):
    return kube("-n", NS, "exec", "-i", pod, "--", *args, input=input).stdout


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def save():
    REPORT.write_text(json.dumps(report, indent=2) + "\n")


def record(name, function):
    started = time.monotonic()
    details = function()
    report["checks"].append({"check": name, "passed": True,
                             "seconds": round(time.monotonic() - started, 2), "details": details})
    save()
    print(json.dumps(report["checks"][-1]), flush=True)


def answer(server, port, name, tcp=False, external=False):
    args = ["dig", "+short", "+time=2", "+tries=1", "@" + server, "-p", str(port), name, "A"]
    if tcp:
        args.append("+tcp")
    return (run(args).stdout if external else execute(CLIENT, *args)).strip()


def dns():
    service = get("service", "pihole-staging-dns")
    results = {}
    for tcp in (False, True):
        protocol = "TCP" if tcp else "UDP"
        targets = [("service", "pihole-staging-dns", 53, False)]
        nodeport = next(p["nodePort"] for p in service["spec"]["ports"] if p["protocol"] == protocol)
        for pod, node in zip(PODS, ("192.168.1.73", "192.168.1.77")):
            targets.append((pod, get("pod", pod)["status"]["podIP"], 5353, False))
            targets.append((node, node, nodeport, True))
        for label, server, port, external in targets:
            for name, expected in (("staging-health.invalid", "192.0.2.42"), ("staging-blocked.invalid", "0.0.0.0")):
                require(answer(server, port, name, tcp, external) == expected, label + " " + protocol + " " + name + " mismatch")
            upstream = answer(server, port, "example.com", tcp, external).splitlines()
            require(upstream and any(ipaddress.ip_address(a).version == 4 for a in upstream), "upstream lookup failed")
            results[label + "/" + protocol] = "local, blocked, upstream passed"
    return results


def metrics():
    text = execute(CLIENT, "curl", "-fsS", "--max-time", "15", "http://pihole-staging-metrics:9617/metrics")
    for pod in PODS:
        samples = [line for line in text.splitlines() if line.startswith("pihole_status{") and pod + "." in line]
        require(len(samples) == 1 and float(samples[0].rsplit(" ", 1)[1]) == 1, "missing active blocking status for " + pod)
    return {"authenticated_targets": PODS, "blocking_status": 1}


def placement():
    nodes = [get("pod", pod)["spec"]["nodeName"] for pod in PODS]
    require(nodes == ["cp01", "cp03"], "unexpected staging placement")
    claims = {}
    for ordinal in range(2):
        claim = get("pvc", f"etc-pihole-pihole-staging-{ordinal}")
        require(claim["status"]["phase"] == "Bound", "claim is not bound")
        require(claim["spec"]["volumeName"] == f"pihole-staging-{ordinal}", "wrong PV binding")
        claims[claim["metadata"]["name"]] = claim["spec"]["volumeName"]
    for service in ("pihole-staging-dns", "pihole-staging-headless"):
        slices = json.loads(kube("-n", NS, "get", "endpointslices", "-l", "kubernetes.io/service-name=" + service, "-o", "json").stdout)["items"]
        targets = sorted({e["targetRef"]["name"] for s in slices for e in s.get("endpoints", [])})
        require(targets == PODS, "Service selected unrelated pods")
    pdb = get("pdb", "pihole-staging")["status"]
    require(pdb["currentHealthy"] == 2 and pdb["expectedPods"] == 2, "incorrect PDB health/selection")
    return {"nodes": nodes, "claims": claims, "pdb_healthy": pdb["currentHealthy"]}


def persistence():
    results = {}
    for ordinal, pod in enumerate(PODS):
        old = get("pod", pod)["metadata"]["uid"]
        claim = f"etc-pihole-pihole-staging-{ordinal}"
        claim_uid = get("pvc", claim)["metadata"]["uid"]
        marker = secrets.token_hex(16)
        # The marker is not a credential. Avoid exec stdin transfer ambiguity and
        # verify the write before deleting the pod, so transfer failures cannot
        # be misreported as storage data loss.
        execute(pod, "sh", "-c", 'printf "%s" "$1" > /etc/pihole/.staging-persistence-proof; sync /etc/pihole/.staging-persistence-proof', "sh", marker)
        require(execute(pod, "cat", "/etc/pihole/.staging-persistence-proof") == marker, "test marker write failed before pod replacement")
        stop = threading.Event()
        traffic = {"queries": 0, "failures": 0}

        def query_loop():
            while not stop.is_set():
                for tcp in (False, True):
                    traffic["queries"] += 1
                    try:
                        if answer("pihole-staging-dns", 53, "staging-health.invalid", tcp) != "192.0.2.42":
                            traffic["failures"] += 1
                    except Exception:
                        traffic["failures"] += 1
                stop.wait(0.3)

        thread = threading.Thread(target=query_loop)
        thread.start()
        try:
            kube("-n", NS, "delete", "pod", pod, "--wait=true", "--timeout=90s")
            kube("-n", NS, "wait", "--for=condition=Ready", "pod/" + pod, "--timeout=150s")
            require(get("pod", pod)["metadata"]["uid"] != old, "pod was not replaced")
            require(get("pvc", claim)["metadata"]["uid"] == claim_uid, "PVC identity changed")
            require(execute(pod, "cat", "/etc/pihole/.staging-persistence-proof") == marker, "persistent marker lost")
            count = execute(pod, "pihole-FTL", "sqlite3", "/etc/pihole/gravity.db", "SELECT COUNT(*) FROM domainlist WHERE domain='staging-blocked.invalid' AND enabled=1;").strip()
            require(count == "1", "managed policy missing or duplicated after restart")
            execute(pod, "rm", "/etc/pihole/.staging-persistence-proof")
        finally:
            stop.set()
            thread.join(timeout=30)
        require(traffic["queries"] > 0, "no availability queries ran")
        results[pod] = {"same_pvc": True, "marker_retained": True, "policy_entries": int(count), **traffic}
    return results


def rotate():
    # Retain v1 for rollback. Immutable v2 is deliberately a create, not an overwrite.
    credential = secrets.token_urlsafe(36) + ",staging"
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "pihole-staging-web-v2", "namespace": NS,
              "labels": {"app.kubernetes.io/instance": NS}}, "immutable": True, "type": "Opaque",
              "data": {"password": base64.b64encode(credential.encode()).decode()}}
    kube("create", "-f", "-", input=json.dumps(secret))
    # Make the complete desired non-secret configuration reviewable and repeatable.
    values = ROOT / "deploy/staging/values.yaml"
    original = values.read_text()
    require("pihole-staging-web-v1" in original and "staging-v1" in original, "rotation source is not v1")
    values.write_text(original.replace("pihole-staging-web-v1", "pihole-staging-web-v2").replace("staging-v1", "staging-v2"))
    run(["helm", "upgrade", NS, str(ROOT / "charts/pihole"), "--kube-context", CONTEXT, "--namespace", NS,
         "-f", str(values), "-f", str(ROOT / "deploy/staging/values-homelab.yaml"), "--wait", "--timeout", "3m"])
    for pod in PODS:
        address = get("pod", pod)["status"]["podIP"]
        url = "http://" + address + ":8080/api/auth"
        response = json.loads(execute(CLIENT, "curl", "-fsS", "--max-time", "10", "-H", "Content-Type: application/json", "--data-binary", "@-", url, input=json.dumps({"password": credential}) + "\n"))
        require(response.get("session", {}).get("valid") is True, "rotated credential did not authenticate")
        sid = response["session"]["sid"]
        execute(CLIENT, "sh", "-c", 'read -r sid; curl -fsS --max-time 10 -X DELETE -H "sid: $sid" "$1"', "sh", url, input=sid + "\n")
    return {"current_secret": "pihole-staging-web-v2", "rollback_secret_retained": True, "both_replicas_authenticated": True}


def prometheus():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(["kubectl", "--context", CONTEXT, "-n", "monitoring", "port-forward", "service/monitoring-stack-prometheus", f"{port}:9090", "--address", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 80
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/targets", timeout=5) as response:
                    targets = json.load(response)["data"]["activeTargets"]
                targets = [t for t in targets if t.get("labels", {}).get("namespace") == NS and t["labels"].get("service") == "pihole-staging-metrics"]
                if targets and all(t["health"] == "up" for t in targets):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/rules", timeout=5) as response:
                        groups = json.load(response)["data"]["groups"]
                    expected = {"PiholeReplicasUnavailable", "PiholeExporterUnavailable", "PiholeBlockingDisabled", "PiholeSyntheticDnsStale"}
                    rules = [r for g in groups for r in g["rules"] if r["name"] in expected]
                    query = urllib.parse.urlencode({"query": 'pihole_status{namespace="pihole-staging",service="pihole-staging-metrics"}'})
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/query?{query}", timeout=5) as response:
                        samples = json.load(response)["data"]["result"]
                    if ({r["name"] for r in rules} == expected and
                            all(r.get("health") == "ok" and r.get("state") == "inactive" for r in rules) and
                            len(samples) == 2 and all(float(s["value"][1]) == 1 for s in samples)):
                        return {"healthy_scrape_targets": len(targets), "last_scrape": [t["lastScrape"] for t in targets],
                                "authenticated_status_samples": len(samples), "alert_rules": {r["name"]: r["state"] for r in rules},
                                "notification_delivery_tested": False, "scheduled_synthetic_dns_monitor_installed": True,
                                "synthetic_scope": "in-cluster Service and all replicas; one-minute schedule"}
            except (OSError, ValueError):
                pass
            time.sleep(2)
        raise AssertionError("Prometheus did not report a healthy scrape, two blocking-status samples and four healthy inactive rules")
    finally:
        process.terminate()
        process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate-credentials", action="store_true", help="Perform the one-time staging v1-to-v2 credential rotation")
    parser.add_argument("--output", type=Path, help="Write new evidence separately to preserve an earlier validation report")
    args = parser.parse_args()
    global REPORT
    if args.output:
        REPORT = args.output
    if args.rotate_credentials:
        source = (ROOT / "deploy/staging/values.yaml").read_text()
        require("pihole-staging-web-v1" in source and "staging-v1" in source, "rotation is only valid for a v1 staging deployment")
        require(kube("-n", NS, "get", "secret", "pihole-staging-web-v2", check=False).returncode != 0, "v2 credential Secret already exists")
    created = False
    try:
        require(get("statefulset", NS)["spec"]["replicas"] == 2, "unexpected release")
        require(get("statefulset", NS)["spec"]["template"]["spec"]["containers"][0]["image"] == IMAGE, "unexpected image")
        pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": CLIENT, "namespace": NS, "labels": {"app.kubernetes.io/component": "test"}},
               "spec": {"nodeSelector": {"kubernetes.io/hostname": "cp02"}, "automountServiceAccountToken": False,
                        "securityContext": {"runAsUser": 1000, "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                        "containers": [{"name": "client", "image": IMAGE, "command": ["/bin/sh", "-c", "sleep 1800"],
                                        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                                        "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}}}]}}
        kube("create", "-f", "-", input=json.dumps(pod))
        created = True
        kube("-n", NS, "wait", "--for=condition=Ready", "pod/" + CLIENT, "--timeout=90s")
        record("independent_persistent_volumes_and_selection", placement)
        record("udp_tcp_service_pod_and_workstation_nodeport", dns)
        record("authenticated_exporter", metrics)
        record("sequential_pod_replacement_and_persistence", persistence)
        if args.rotate_credentials:
            record("versioned_secret_rotation", rotate)
            record("authenticated_exporter_after_rotation", metrics)
            record("dns_after_rotation", dns)
        record("prometheus_live_scrape", prometheus)
    except Exception as exc:
        report["error"] = str(exc)[:2000]
        print("ERROR " + report["error"], flush=True)
    finally:
        if created:
            report["validation_pod_removed"] = kube("-n", NS, "delete", "pod", CLIENT, "--timeout=60s", check=False).returncode == 0
        report["staging_retained"] = True
        report["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
    if report.get("error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
