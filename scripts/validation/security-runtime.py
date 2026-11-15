#!/usr/bin/env python3
"""Exercise the pinned stock Pi-hole entrypoint in isolated Docker containers.

This is a Docker compatibility probe, not Kubernetes Pod Security certification.
It creates and deletes only uniquely named containers/volumes belonging to this run.
"""

import argparse
import datetime
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path


DEFAULT_IMAGE = (
    "pihole/pihole:2026.10.0@sha256:"
    "d34134716fc32038b052a8674c7c6879e0ff21f0e6d8af345166fffa2c27c113"
)
CAPABILITIES = [
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "SETPCAP",
    "SETFCAP", "NET_BIND_SERVICE", "KILL",
]
REDUCED_FLAGS = ["--cap-drop=ALL"] + ["--cap-add=" + c for c in CAPABILITIES]
CASES = {
    "stock": [],
    "stock_no_new_privileges": ["--security-opt=no-new-privileges:true"],
    "no_new_privileges_privileged_ports": [
        "--security-opt=no-new-privileges:true", "--sysctl=net.ipv4.ip_unprivileged_port_start=1024",
    ],
    "reduced_capabilities": REDUCED_FLAGS,
    "reduced_capabilities_no_new_privileges": REDUCED_FLAGS + [
        "--security-opt=no-new-privileges:true",
    ],
    "nonroot": ["--user=1000:1000", "--cap-drop=ALL", "--cap-add=NET_BIND_SERVICE"],
    "read_only_root": [
        "--read-only", "--tmpfs=/run", "--tmpfs=/tmp",
        "--tmpfs=/var/log/pihole", "--tmpfs=/var/lib/logrotate",
        "--tmpfs=/var/spool/cron/crontabs",
    ],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--output", default="docs/validation/security-runtime.json")
    parser.add_argument("--startup-timeout", type=int, default=120)
    parser.add_argument("--stability-seconds", type=int, default=15)
    parser.add_argument("--dns-port", type=int, default=53)
    parser.add_argument("--no-bootstrap-dns", action="store_true")
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=list(CASES))
    args = parser.parse_args()
    password = secrets.token_urlsafe(24)
    run_id = uuid.uuid4().hex[:10]
    prefix = "pihole-audit-sec-" + run_id
    created = []
    env = dict(os.environ, FTLCONF_webserver_api_password=password)

    def docker(*command, timeout=30, check=False):
        result = subprocess.run(
            ["docker", *command], text=True, capture_output=True,
            timeout=timeout, env=env,
        )
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).replace(password, "[REDACTED]"))
        return result

    def state(name):
        result = docker("inspect", "--format={{json .State}}", name)
        return json.loads(result.stdout) if result.returncode == 0 else {}

    report = {
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "image": args.image,
        "scope": "Docker stock-entrypoint compatibility; not Kubernetes certification",
        "network": "none; no published ports, host networking, or host bind mounts",
        "fixture": {
            "adlists": "empty file on a fresh named volume, initially owned by uid/gid 1000",
            "dns_record": "192.0.2.10 health.audit.test",
            "dns_port": args.dns_port,
            "web_port": 8080,
            "upstream": "192.0.2.53 (unreachable on purpose)",
            "bootstrap_dns_check": "unavailable" if args.no_bootstrap_dns else "static TEST-NET hosts entries for raw.githubusercontent.com and github.com; no external requests",
            "dhcp": False, "ntp_servers_and_sync": False, "nice": 0,
            "memory": "512m", "cpus": 2,
        },
        "startup_timeout_seconds": args.startup_timeout,
        "stability_seconds": args.stability_seconds,
        "reduced_capabilities_candidate": CAPABILITIES,
        "cases": [],
        "cleanup_errors": [],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(report, indent=2) + "\n")

    try:
        report["docker_version"] = json.loads(docker(
            "version", "--format={{json .Server}}", check=True,
        ).stdout)
        report["image_metadata"] = json.loads(docker(
            "image", "inspect", "--format={{json .}}", args.image, check=True,
        ).stdout)
        # Do not persist the image environment or other unnecessary configuration.
        report["image_metadata"] = {
            k: report["image_metadata"].get(k)
            for k in ("Id", "RepoDigests", "Architecture", "Os", "Created")
        }
        for key in args.cases:
            name = prefix + "-" + key.replace("_", "-")
            volume = name + "-data"
            seed = name + "-seed"
            created.append((name, seed, volume))
            docker("volume", "create", volume, check=True)
            docker(
                "run", "--rm", "--name", seed, "--network=none",
                "--entrypoint=/bin/sh", "--mount=type=volume,src=" + volume + ",dst=/etc/pihole",
                args.image, "-c", ": > /etc/pihole/adlists.list; chown -R 1000:1000 /etc/pihole",
                check=True,
            )
            command = [
                "run", "-d", "--name", name, "--network=none", "--memory=512m", "--cpus=2",
                *([] if args.no_bootstrap_dns else ["--add-host=raw.githubusercontent.com:192.0.2.1", "--add-host=github.com:192.0.2.1"]),
                "--mount=type=volume,src=" + volume + ",dst=/etc/pihole",
                "-e", "FTLCONF_webserver_api_password",
                "-e", "FTLCONF_dns_hosts=192.0.2.10 health.audit.test",
                "-e", "FTLCONF_dns_upstreams=192.0.2.53",
                "-e", "FTLCONF_dns_listeningMode=ALL", "-e", "FTLCONF_dns_port=" + str(args.dns_port),
                "-e", "FTLCONF_webserver_port=8080", "-e", "FTLCONF_dhcp_active=false",
                "-e", "FTLCONF_ntp_ipv4_active=false", "-e", "FTLCONF_ntp_ipv6_active=false",
                "-e", "FTLCONF_ntp_sync_active=false", "-e", "FTLCONF_misc_nice=0",
                "-e", "TZ=UTC", *CASES[key], args.image,
            ]
            started = time.monotonic()
            launched = docker(*command)
            result = {
                "case": key, "docker_flags": CASES[key], "launch_exit_code": launched.returncode,
                "dns_ready": False, "stable": False,
                "expected_dns_ready": not args.no_bootstrap_dns and key not in ("nonroot", "read_only_root") and not (
                    key == "no_new_privileges_privileged_ports" and args.dns_port < 1024
                ),
            }
            print("START " + key, flush=True)
            if launched.returncode:
                result["launch_error"] = launched.stderr.replace(password, "[REDACTED]")
            else:
                deadline = started + args.startup_timeout
                while time.monotonic() < deadline:
                    current_state = state(name)
                    if not current_state.get("Running"):
                        break
                    answer = docker(
                        "exec", name, "dig", "+short", "+time=1", "+retry=0",
                        "@127.0.0.1", "-p", str(args.dns_port), "health.audit.test", "A", timeout=5,
                    )
                    if answer.returncode == 0 and answer.stdout.strip() == "192.0.2.10":
                        result["dns_ready"] = True
                        result["seconds_to_dns_answer"] = round(time.monotonic() - started, 2)
                        break
                    time.sleep(2)
                if result["dns_ready"]:
                    time.sleep(args.stability_seconds)
                    answer = docker(
                        "exec", name, "dig", "+short", "+time=1", "+retry=0",
                        "@127.0.0.1", "-p", str(args.dns_port), "health.audit.test", "A", timeout=5,
                    )
                    result["stable"] = answer.returncode == 0 and answer.stdout.strip() == "192.0.2.10"
                    result["ftl_process_status"] = docker(
                        "exec", name, "sh", "-c",
                        'p=$(pidof pihole-FTL); set -- $p; [ "$#" -gt 0 ] && '
                        'sed -n "/^Uid:/p; /^Gid:/p; /^Cap/p; /^NoNewPrivs:/p" /proc/"$1"/status',
                    ).stdout
                    result["ftl_file_capabilities"] = docker(
                        "exec", name, "getcap", "/usr/bin/pihole-FTL",
                    ).stdout.strip()
                result["state_before_cleanup"] = state(name)
                if result["state_before_cleanup"].get("Running"):
                    result["unprivileged_port_start"] = docker(
                        "exec", name, "cat", "/proc/sys/net/ipv4/ip_unprivileged_port_start",
                    ).stdout.strip()
                logs = docker("logs", "--tail=250", name)
                result["logs"] = (logs.stdout + logs.stderr).replace(password, "[REDACTED]")
            report["cases"].append(result)
            result["matches_expected_behavior"] = result["dns_ready"] == result["expected_dns_ready"]
            save()
            print("RESULT " + key + " " + json.dumps({
                "dns_ready": result["dns_ready"], "stable": result["stable"],
                "exit_code": result.get("state_before_cleanup", {}).get("ExitCode"),
            }), flush=True)
            docker("rm", "-f", name)
            docker("volume", "rm", volume)
    finally:
        for name, seed, volume in created:
            for resource in (name, seed):
                inspected = docker("inspect", resource)
                if inspected.returncode == 0:
                    removed = docker("rm", "-f", resource)
                    if removed.returncode:
                        report["cleanup_errors"].append(removed.stderr)
            inspected = docker("volume", "inspect", volume)
            if inspected.returncode == 0:
                removed = docker("volume", "rm", volume)
                if removed.returncode:
                    report["cleanup_errors"].append(removed.stderr)
        report["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
    if len(report["cases"]) != len(args.cases) or report["cleanup_errors"] or any(
        not case["matches_expected_behavior"] or (case["expected_dns_ready"] and not case["stable"])
        for case in report["cases"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
