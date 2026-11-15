#!/usr/bin/env python3
"""Isolated, pinned-image API/DNS/persistence/exporter compatibility experiment.

Requires Docker. Uses only new, uniquely named resources; publishes no host ports.
Fixture credentials are generated for the run and never included in the report.
This is not an implementation of the proposed production policy reconciler.
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.parse import quote
import uuid

PIHOLE = "pihole/pihole:2026.10.0@sha256:d34134716fc32038b052a8674c7c6879e0ff21f0e6d8af345166fffa2c27c113"
EXPORTER = "ekofr/pihole-exporter:v1.3.0@sha256:67dd0ec8c0fbc2f4edeffd6b0c6768f2b962dcc983b230bedd933a33e5a05cd9"
FIXTURE = "python:3.13-alpine@sha256:8bcfa47b04b853a17aaf909362b847b2112fd6ec4105ea553ef9a43bafbb5916"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/validation/api-runtime.json")
    args = parser.parse_args()
    prefix = "pihole-audit-api-" + uuid.uuid4().hex[:8]
    network = prefix + "-net"
    names = [prefix + "-a", prefix + "-b"]
    fixture, exporter = prefix + "-lists", prefix + "-exporter"
    containers, volumes = [], []
    password = secrets.token_urlsafe(24)
    process_env = dict(os.environ, FTLCONF_webserver_api_password=password, PIHOLE_PASSWORD=password)
    report = {
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "images": {"pihole": PIHOLE, "exporter": EXPORTER, "fixture": FIXTURE},
        "scope": "amd64 Docker API/DNS/persistence smoke; no cluster HA claim",
        "network": "internal bridge; no host ports or host bind mounts",
        "bootstrap_dns_check": "static TEST-NET hosts entries for raw.githubusercontent.com and github.com",
        "checks": [], "cleanup_errors": [],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(report, indent=2) + "\n")

    def docker(*command, input=None, check=True, timeout=90):
        p = subprocess.run(["docker", *command], input=input, capture_output=True,
                           text=True, timeout=timeout, env=process_env)
        if check and p.returncode:
            raise RuntimeError((p.stderr or p.stdout)[:1200].replace(password, "[REDACTED]"))
        return p

    def record(name, function):
        started = time.monotonic()
        try:
            details = function()
            result = {"check": name, "passed": True, "details": details}
        except Exception as exc:
            result = {"check": name, "passed": False, "error": str(exc).replace(password, "[REDACTED]")[:1600]}
        result["seconds"] = round(time.monotonic() - started, 2)
        report["checks"].append(result)
        save()
        print(json.dumps(result), flush=True)
        return result

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def http(container, method, url, body=None, sid=None):
        command = ["exec", "-i", container, "curl", "--silent", "--show-error",
                   "--max-time", "20", "-X", method, "-w", "\n%{http_code}"]
        if sid:
            command += ["-H", "sid: " + sid]
        if body is not None:
            command += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        p = docker(*command, url, input=json.dumps(body) if body is not None else None)
        text, code = p.stdout.rsplit("\n", 1)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = text
        return int(code), data

    def api(method, path, body=None, sid=None, target=None):
        code, data = http(target or names[0], method, "http://127.0.0.1:8080/api/" + path, body, sid)
        if code >= 400:
            raise RuntimeError(f"{method} {path}: HTTP {code}: {str(data)[:700]}")
        return data

    def auth(target=None, cli=False):
        credential = password
        if cli:
            credential = docker("exec", target or names[0], "cat", "/etc/pihole/cli_pw").stdout.strip()
        data = api("POST", "auth", {"password": credential}, target=target)
        require(data.get("session", {}).get("valid"), "authentication did not establish a valid session")
        return data["session"]["sid"]

    def dig(target, domain, tcp=False):
        return docker("exec", target, "dig", "+short", "+time=2", "+retry=0",
                      *( ["+tcp"] if tcp else [] ), "@127.0.0.1", domain, "A").stdout.strip()

    def wait_dns(target, seconds=100):
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            state = json.loads(docker("inspect", "--format", "{{json .State}}", target).stdout)
            if not state["Running"]:
                raise RuntimeError(f"container exited: {state.get('ExitCode')}")
            try:
                if dig(target, "health.audit.test") == "192.0.2.123":
                    return round(time.monotonic() - start, 2)
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError("local DNS did not become ready within budget")

    try:
        report["engine"] = json.loads(docker("version", "--format", "{{json .Server}}").stdout)
        docker("network", "create", "--internal", network)
        with tempfile.TemporaryDirectory(prefix="pihole-audit-fixture-") as tmp:
            root = Path(tmp)
            (root / "list-v1.txt").write_text("0.0.0.0 blocked.audit.test\n")
            (root / "list-v2.txt").write_text("0.0.0.0 alternate.audit.test\n")
            containers.append(fixture)
            docker("create", "--name", fixture, "--network", network, "--network-alias", "fixture",
                   "--memory", "96m", "--cpus", "0.5", FIXTURE,
                   "python", "-m", "http.server", "8080", "--directory", "/fixtures")
            docker("cp", str(root) + "/.", fixture + ":/fixtures")
            docker("start", fixture)
        for name in names:
            volume = name + "-data"
            volumes.append(volume)
            docker("volume", "create", volume)
            seed = name + "-seed"
            containers.append(seed)
            docker("run", "--rm", "--name", seed, "--network", "none", "--entrypoint", "/bin/sh",
                   "-v", volume + ":/etc/pihole", PIHOLE, "-c",
                   "printf '%s\\n' 'http://fixture:8080/list-v1.txt' > /etc/pihole/adlists.list")
            containers.append(name)
            docker("run", "-d", "--name", name, "--network", network,
                   "--add-host", "raw.githubusercontent.com:192.0.2.1", "--add-host", "github.com:192.0.2.1",
                   "--memory", "512m", "--cpus", "1", "--security-opt", "no-new-privileges=true",
                   "-v", volume + ":/etc/pihole", "-e", "FTLCONF_webserver_api_password",
                   "-e", "FTLCONF_webserver_port=8080", "-e", "FTLCONF_webserver_session_restore=false",
                   "-e", "FTLCONF_dns_listeningMode=ALL", "-e", "FTLCONF_dns_upstreams=192.0.2.53",
                   "-e", "FTLCONF_dns_hosts=192.0.2.123 health.audit.test;192.0.2.10 allowed.audit.test;192.0.2.11 blocked.audit.test;192.0.2.12 exact.audit.test;192.0.2.13 alternate.audit.test",
                   "-e", "FTLCONF_ntp_ipv4_active=false", "-e", "FTLCONF_ntp_ipv6_active=false",
                   "-e", "FTLCONF_ntp_sync_active=false", "-e", "FTLCONF_misc_nice=0",
                   "-e", "FTLCONF_database_maxDBdays=1", PIHOLE)
            result = record("startup_" + name[-1], lambda n=name: {"dns_ready_after_seconds": wait_dns(n)})
            require(result["passed"], "startup failed; dependent tests skipped")

        report["ftl_version"] = docker("exec", names[0], "pihole-FTL", "--version").stdout.strip()
        sid = auth()
        record("api_authentication", lambda: {"admin_session_valid": True, "cli_session_valid": bool(auth(cli=True))})

        def dns_fixture():
            answers = {protocol: {domain: dig(names[0], domain, protocol == "tcp")
                                  for domain in ("health.audit.test", "allowed.audit.test", "blocked.audit.test")}
                       for protocol in ("udp", "tcp")}
            for protocol in answers:
                require(answers[protocol]["health.audit.test"] == "192.0.2.123", "health response differs")
                require(answers[protocol]["allowed.audit.test"] == "192.0.2.10", "allow response differs")
                require(answers[protocol]["blocked.audit.test"] == "0.0.0.0", "fixture blocklist not applied")
            return answers
        record("udp_tcp_filtering", dns_fixture)

        comment = "literal apostrophe ' double quote \" dollar $VALUE backtick `literal`"
        def group_client_crud():
            api("POST", "groups", {"name": "audit-group", "comment": comment, "enabled": True}, sid)
            groups = api("GET", "groups", sid=sid)["groups"]
            group = next(g for g in groups if g["name"] == "audit-group")
            require(group["comment"] == comment, "structured text roundtrip differs")
            group_id = group["id"]
            api("PUT", "groups/audit-group", {"name": "audit-group", "enabled": False, "comment": "updated"}, sid)
            require(not next(g for g in api("GET", "groups", sid=sid)["groups"] if g["id"] == group_id)["enabled"], "group update failed")
            api("POST", "clients", {"client": "192.0.2.22", "comment": comment, "groups": [group_id]}, sid)
            client = next(c for c in api("GET", "clients", sid=sid)["clients"] if c["client"] == "192.0.2.22")
            require(client["groups"] == [group_id], "explicit membership retained default group unexpectedly")
            api("DELETE", "clients/192.0.2.22", sid=sid)
            api("DELETE", "groups/audit-group", sid=sid)
            require(not any(g["name"] == "audit-group" for g in api("GET", "groups", sid=sid)["groups"]), "group delete failed")
            return {"create_update_delete": True, "literal_text_preserved": True, "explicit_group_membership": client["groups"], "default_group_zero_preserved": any(g["id"] == 0 for g in groups)}
        record("group_client_crud", group_client_crud)

        def domain_crud():
            cli_sid = auth(cli=True)
            api("POST", "domains/deny/exact", {"domain": "exact.audit.test", "comment": comment, "groups": [0]}, cli_sid)
            time.sleep(1)
            require(dig(names[0], "exact.audit.test") == "0.0.0.0", "exact rule not effective")
            api("PUT", "domains/deny/exact/exact.audit.test", {"enabled": False, "comment": "updated", "groups": [0]}, cli_sid)
            time.sleep(1)
            require(dig(names[0], "exact.audit.test") == "192.0.2.12", "disabled rule still blocks")
            api("DELETE", "domains/deny/exact/exact.audit.test", sid=cli_sid)
            api("DELETE", "auth", sid=cli_sid)
            return {"cli_credential_policy_mutation": True, "create_update_delete": True}
        record("domain_crud", domain_crud)

        def list_crud():
            address = "http://fixture:8080/list-v2.txt"
            api("POST", "lists?type=block", {"address": address, "comment": comment, "groups": [0]}, sid)
            lists = api("GET", "lists?type=block", sid=sid)["lists"]
            require(any(item["address"] == address for item in lists), "subscription was not created")
            path = "lists/" + quote(address, safe="") + "?type=block"
            api("PUT", path, {"enabled": False, "comment": "updated", "groups": [0]}, sid)
            api("DELETE", path, sid=sid)
            require(not any(item["address"] == address for item in api("GET", "lists?type=block", sid=sid)["lists"]), "subscription was not deleted")
            return {"create_update_delete": True}
        record("list_crud", list_crud)

        def persisted_restart():
            docker("stop", "--time", "15", fixture)
            docker("restart", "--time", "15", names[0])
            elapsed = wait_dns(names[0])
            require(dig(names[0], "blocked.audit.test") == "0.0.0.0", "persisted blocking was lost")
            require(dig(names[0], "allowed.audit.test", True) == "192.0.2.10", "persisted local resolution was lost")
            return {"list_provider_unavailable": True, "dns_ready_after_seconds": elapsed, "existing_gravity_still_effective": True}
        record("restart_without_list_provider", persisted_restart)

        containers.append(exporter)
        docker("run", "-d", "--name", exporter, "--network", network, "--memory", "128m", "--cpus", "0.5",
               "-e", "PIHOLE_HOSTNAME=" + ",".join(names), "-e", "PIHOLE_PORT=8080,8080",
               "-e", "PIHOLE_PROTOCOL=http,http", "-e", "PIHOLE_PASSWORD", "-e", "TIMEOUT=2s", EXPORTER)
        time.sleep(2)

        def scrape():
            code, body = http(names[0], "GET", "http://" + exporter + ":9617/metrics")
            require(code == 200 and isinstance(body, str), "exporter metrics unavailable")
            return body

        def metrics_auth():
            body = scrape()
            samples = [line for line in body.splitlines() if line.startswith("pihole_")]
            require(samples, "no Pi-hole metric samples")
            require(all(name in body for name in names), "not all target hostnames represented")
            return {"http_status": 200, "pihole_sample_count": len(samples), "both_targets_present": True}
        record("exporter_authenticated_targets", metrics_auth)

        def metrics_failure():
            docker("stop", "--time", "15", names[1])
            body = scrape()
            stale = any(names[1] in line for line in body.splitlines() if line.startswith("pihole_"))
            require(stale, "expected retained samples not observed")
            docker("start", names[1])
            wait_dns(names[1])
            scrape()
            return {"http_status_while_target_down": 200, "down_target_samples_retained": stale, "scrape_after_target_restart": True}
        record("exporter_target_failure_and_recovery", metrics_failure)
    except Exception as exc:
        report["fatal_error"] = str(exc).replace(password, "[REDACTED]")[:1600]
        print("ERROR " + report["fatal_error"], flush=True)
    finally:
        for name in reversed(containers):
            if docker("inspect", name, check=False).returncode == 0:
                p = docker("rm", "-f", name, check=False)
                if p.returncode:
                    report["cleanup_errors"].append(p.stderr[:300])
        for name in volumes:
            p = docker("volume", "rm", name, check=False)
            if p.returncode:
                report["cleanup_errors"].append(p.stderr[:300])
        docker("network", "rm", network, check=False)
        report["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
    if report.get("fatal_error") or report["cleanup_errors"] or any(not item["passed"] for item in report["checks"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
