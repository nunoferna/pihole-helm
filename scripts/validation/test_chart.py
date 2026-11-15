#!/usr/bin/env python3
"""Chart contract regressions. Run: python3 -m unittest discover -s scripts/validation -p test_chart.py."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts/pihole"


def render(values=None, valid=True, chart=CHART):
    merged = {"pihole": {"web": {"password": "fixture-credential-not-for-production"}}}
    def merge(dest, source):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(dest.get(key), dict):
                merge(dest[key], value)
            else:
                dest[key] = value
    merge(merged, values or {})
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "values.yaml"
        path.write_text(yaml.safe_dump(merged))
        result = subprocess.run(["helm", "template", "audit", str(chart), "--namespace", "fixture", "-f", str(path)], capture_output=True, text=True)
    if valid and result.returncode:
        raise AssertionError(result.stderr)
    if not valid:
        if not result.returncode:
            raise AssertionError("Invalid configuration unexpectedly rendered")
        return result.stderr
    return [x for x in yaml.safe_load_all(result.stdout) if x]


def find(objects, kind, suffix=None):
    return next(x for x in objects if x["kind"] == kind and (suffix is None or x["metadata"]["name"].endswith(suffix)))


def container(objects, kind="StatefulSet"):
    return find(objects, kind)["spec"]["template"]["spec"]["containers"][0]


def environment(objects, kind="StatefulSet"):
    return {x["name"]: x for x in container(objects, kind)["env"]}


class ChartContract(unittest.TestCase):
    def test_service_values_migration(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("migrate_values", ROOT / "scripts/migrate-values.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        legacy = {"pihole": {"web": {"service": {"type": "ClusterIP"}}, "ftl": {"dns": {"service": {"type": "NodePort"}}, "dhcp": {"ipv6": {"active": True, "service": {"enabled": True}}}}}}
        migrated = module.migrate(legacy)
        render(legacy, valid=False)
        self.assertIn("Move Kubernetes Service", render({"pihole": {"web": {"service": {"type": "ClusterIP"}}}}, valid=False))
        objects = render(migrated)
        self.assertEqual(find(objects, "Service", "-dns")["spec"]["type"], "NodePort")
        self.assertEqual(environment(objects)["FTLCONF_dhcp_ipv6"]["value"], "true")
        self.assertEqual(module.migrate(migrated), migrated)
        self.assertIn("service", legacy["pihole"]["ftl"]["dns"])
        legacy["services"] = {"dns": {"type": "LoadBalancer"}}
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            module.migrate(legacy)

    def test_deterministic_credentials_and_rollout(self):
        a, b = render(), render()
        self.assertEqual(a, b)
        changed = render({"pihole": {"web": {"password": "another-fixture"}}, "metrics": {"enabled": True}})
        old = render({"metrics": {"enabled": True}})
        for kind in ("StatefulSet", "Deployment"):
            self.assertNotEqual(find(old, kind)["spec"]["template"]["metadata"]["annotations"]["checksum/credentials"], find(changed, kind)["spec"]["template"]["metadata"]["annotations"]["checksum/credentials"])

    def test_external_secret_and_revision(self):
        values = {"pihole": {"web": {"password": "", "existingSecret": "managed-v1"}}, "metrics": {"enabled": True}, "credentialsRevision": "rotation-2"}
        objects = render(values)
        self.assertFalse(any(o["kind"] == "Secret" for o in objects))
        for kind in ("StatefulSet", "Deployment"):
            self.assertEqual(find(objects, kind)["spec"]["template"]["metadata"]["annotations"]["credentials-revision"], "rotation-2")
        self.assertEqual(environment(objects)["FTLCONF_webserver_api_password"]["valueFrom"]["secretKeyRef"]["name"], "managed-v1")
        exporter = find(objects, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(exporter["volumes"][0]["secret"]["secretName"], "managed-v1")
        self.assertIn("PIHOLE_PASSWORD_FILE", environment(objects, "Deployment"))

    def test_credential_errors(self):
        for web in ({"password": ""}, {"existingSecret": "external"}, {"skipPasswordEnv": True}):
            with self.subTest(web=web):
                self.assertIn("Error", render({"pihole": {"web": web}}, valid=False))
        render({"tests": {"authentication": False}, "pihole": {"web": {"password": "", "skipPasswordEnv": True}}, "extraEnv": [{"name": "FTLCONF_webserver_api_password", "valueFrom": {"secretKeyRef": {"name": "injected", "key": "password"}}}]})

    def test_selector_isolation(self):
        objects = render({"metrics": {"enabled": True}, "podDisruptionBudget": {"enabled": True}})
        labels = find(objects, "StatefulSet")["spec"]["template"]["metadata"]["labels"]
        candidates = [find(objects, "Deployment")["spec"]["template"]["metadata"]["labels"]] + [o["metadata"]["labels"] for o in objects if o["kind"] == "Pod"]
        for obj in objects:
            if obj["kind"] == "Service" and not obj["metadata"]["name"].endswith("-metrics"):
                selector = obj["spec"]["selector"]
            elif obj["kind"] == "PodDisruptionBudget":
                selector = obj["spec"]["selector"]["matchLabels"]
            else:
                continue
            self.assertEqual(selector["app.kubernetes.io/component"], "dns")
            for other in candidates:
                self.assertFalse(all(other.get(k) == v for k, v in selector.items()))
        self.assertEqual(labels["app.kubernetes.io/component"], "dns")

    def test_workload_storage_identity(self):
        for replicas in (1, 2, 3):
            with self.subTest(replicas=replicas):
                objects = render({"replicas": replicas})
                workload = find(objects, "StatefulSet")
                self.assertEqual(workload["spec"]["volumeClaimTemplates"][0]["metadata"]["name"], "etc-pihole")
                self.assertEqual(workload["spec"]["persistentVolumeClaimRetentionPolicy"], {"whenDeleted": "Retain", "whenScaled": "Retain"})
                self.assertFalse(any(o["kind"] in ("PersistentVolumeClaim", "Deployment") for o in objects))
        ephemeral = render({"persistence": {"etcPihole": {"enabled": False}}})
        self.assertIn({"name": "etc-pihole", "emptyDir": {}}, find(ephemeral, "StatefulSet")["spec"]["template"]["spec"]["volumes"])
        explicit = render({"persistence": {"etcPihole": {"storageClassName": ""}}})
        self.assertEqual(find(explicit, "StatefulSet")["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"], "")

    def test_ftl_value_semantics(self):
        objects = render({"pihole": {"ftl": {"dns": {"localise": False, "domain": {"local": False, "name": ""}, "cache": {"size": 0}, "hosts": [], "upstreams": []}, "resolver": {"macNames": False}, "database": {"forceDisk": False}}}})
        env = environment(objects)
        for name, value in {"dns_localise": "false", "dns_domain_local": "false", "dns_domain_name": "", "dns_cache_size": "0", "dns_hosts": "", "dns_upstreams": "", "resolver_macNames": "false", "database_forceDisk": "false"}.items():
            self.assertEqual(env["FTLCONF_" + name]["value"], value)
        self.assertFalse(any("_service_" in k for k in env))
        self.assertNotIn("FTLCONF_files_pid", env)

    def test_native_defaults_are_inherited(self):
        env = environment(render())
        for name in ("dns_hosts", "dns_upstreams", "webserver_headers", "webserver_advancedOpts", "misc_dnsmasq_lines"):
            self.assertNotIn("FTLCONF_" + name, env)

    def test_listener_contract(self):
        objects = render({"listeners": {"dns": 6053, "http": 18080, "https": 18443}, "metrics": {"enabled": True}, "networkPolicy": {"enabled": True, "webAllowFrom": [{"podSelector": {}}]}})
        env = environment(objects)
        self.assertEqual(env["FTLCONF_dns_port"]["value"], "6053")
        self.assertEqual(env["FTLCONF_webserver_port"]["value"], "18080,18443s")
        self.assertEqual({p["name"]: p["containerPort"] for p in container(objects)["ports"]}["dns-udp"], 6053)
        service = find(objects, "Service", "-dns")
        self.assertEqual([(p["port"], p["targetPort"]) for p in service["spec"]["ports"]], [(53, "dns-tcp"), (53, "dns-udp")])
        headless = find(objects, "Service", "-headless")
        self.assertEqual(headless["spec"]["ports"][2]["port"], 18080)
        self.assertEqual(environment(objects, "Deployment")["PIHOLE_PORT"]["value"], "18080")
        policy = find(objects, "NetworkPolicy", "audit-pihole")
        self.assertEqual(policy["spec"]["ingress"][0]["ports"][0]["port"], 6053)
        self.assertNotIn(67, [p["port"] for rule in policy["spec"]["ingress"] for p in rule["ports"]])
        self.assertIn("6053", container(objects)["readinessProbe"]["exec"]["command"])

    def test_https_disabled(self):
        objects = render({"listeners": {"https": 0}})
        self.assertEqual(environment(objects)["FTLCONF_webserver_port"]["value"], "8080")
        self.assertEqual([p["name"] for p in find(objects, "Service", "-web")["spec"]["ports"]], ["http"])

    def test_user_web_annotations(self):
        objects = render({'pihole': {'web': {}}, 'services': {'web': {'annotations': {'example.test/purpose': 'admin'}}}})
        self.assertEqual(find(objects, "Service", "-web")["metadata"]["annotations"]["example.test/purpose"], "admin")
        render({"listeners": {"dns": 8080}}, valid=False)

    def test_pinned_image_contract(self):
        for override in ({"tag": "unverified"}, {"digest": ""}):
            render({"image": override}, valid=False)
        objects = render({"image": {"allowUnverified": True, "tag": "custom", "digest": ""}})
        self.assertEqual(container(objects)["image"], "pihole/pihole:custom")

    def test_nodeport_modes(self):
        for explicit in (False, True):
            config = {"type": "NodePort"}
            if explicit:
                config["nodePorts"] = {"tcp": 30053, "udp": 30054}
            objects = render({'pihole': {'ftl': {'dns': {}}, 'web': {}}, 'services': {'dns': config, 'web': {'type': 'NodePort'}}})
            service = find(objects, "Service", "-dns")
            self.assertEqual(service["spec"]["ports"][0].get("nodePort"), 30053 if explicit else None)
        objects = render({'pihole': {'ftl': {'dns': {}}}, 'services': {'dns': {'splitTcpUdp': True, 'tcp': {'type': 'NodePort'}, 'udp': {'type': 'NodePort'}}}})
        self.assertEqual(find(objects, "Service", "-dns-tcp")["spec"]["type"], "NodePort")
        custom = render({"services": {"dns": {"splitTcpUdp": True, "tcp": {"port": 1053}, "udp": {"port": 2053}}}})
        probe_env = {e['name']: e['value'] for e in find(custom, 'Pod')['spec']['containers'][0]['env']}
        self.assertEqual(probe_env['HELM_CHECK_SERVICE_TCP_PORT'], '1053')
        self.assertEqual(probe_env['HELM_CHECK_SERVICE_UDP_PORT'], '2053')

    def test_private_dns_default_and_scoped_load_balancer_fields(self):
        self.assertEqual(find(render(), 'Service', '-dns')['spec']['type'], 'ClusterIP')
        config = {'type': 'LoadBalancer', 'loadBalancerClass': 'example.com/dns', 'loadBalancerSourceRanges': ['192.0.2.0/24']}
        service = find(render({'services': {'dns': config}}), 'Service', '-dns')['spec']
        self.assertEqual(service['loadBalancerClass'], 'example.com/dns')
        self.assertEqual(service['loadBalancerSourceRanges'], ['192.0.2.0/24'])
        config['type'] = 'ClusterIP'
        service = find(render({'services': {'dns': config}}), 'Service', '-dns')['spec']
        self.assertNotIn('loadBalancerClass', service)
        self.assertNotIn('loadBalancerSourceRanges', service)

    def test_explicit_egress_policy_is_role_scoped(self):
        render({'networkPolicy': {'egressEnabled': True}}, valid=False)
        rules = [{'to': [{'ipBlock': {'cidr': '192.0.2.53/32'}}], 'ports': [{'port': 53, 'protocol': 'UDP'}]}]
        objects = render({'networkPolicy': {'enabled': True, 'egressEnabled': True, 'egress': rules}, 'metrics': {'enabled': True}})
        dns = find(objects, 'NetworkPolicy', 'audit-pihole')['spec']
        metrics = find(objects, 'NetworkPolicy', '-metrics')['spec']
        self.assertEqual(dns['policyTypes'], ['Ingress', 'Egress'])
        self.assertEqual(dns['egress'], rules)
        self.assertEqual(metrics['egress'], [])

    def test_reject_invalid_configurations(self):
        for values in ({"replicas": 0}, {"listeners": {"dns": 0}}, {"listeners": {"http": 70000}}, {"replicas": 2, "pihole": {"ftl": {"dhcp": {"active": True}}}}, {"pihole": {"ftl": {"files": {"pid": "/tmp/obsolete"}}}}, {"pihole": {"ftl": {"dns": {"port": 1053}}}}, {"extraEnv": [{"name": "FTLCONF_dns_port", "value": "53"}]}, {"strategy": {"type": "Recreate"}}):
            with self.subTest(values=values):
                render(values, valid=False)

    def test_policy_data_is_not_executable(self):
        literal = "apostrophe ' quote \" dollar $(false) backtick `false`"
        objects = render({"pihole": {"lists": {"blacklist": [{"domain": "blocked.test", "comment": literal}]}}})
        data = find(objects, "ConfigMap")["data"]
        self.assertEqual(json.loads(data["policy.json"])["lists"]["blacklist"][0]["comment"], literal)
        self.assertNotIn(literal, data["import-lists.sh"])
        self.assertNotIn("sqlite3", data["import-lists.sh"])
        self.assertIn('exec timeout', data["import-lists.sh"])

    def test_policy_revision_identity_and_empty_reconciliation(self):
        a = render()
        artifact = find(a, "ConfigMap")
        self.assertTrue(artifact["immutable"])
        self.assertEqual(artifact["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
        import hashlib
        revision = hashlib.sha256(artifact["data"]["policy.json"].encode()).hexdigest()
        self.assertEqual(environment(a)["HELM_POLICY_EXPECTED_REVISION"]["value"], revision)
        volumes = find(a, "StatefulSet")["spec"]["template"]["spec"]["volumes"]
        self.assertEqual(next(v for v in volumes if v["name"] == "lists")["configMap"]["name"], artifact["metadata"]["name"])
        changed = render({"pihole": {"lists": {"blacklist": [{"domain": "blocked.test"}]}}})
        self.assertNotEqual(find(changed, "ConfigMap")["metadata"]["name"], artifact["metadata"]["name"])
        self.assertEqual(find(render({"pihole": {"web": {"password": "other"}}}), "ConfigMap")["metadata"]["name"], artifact["metadata"]["name"])
        self.assertIn("postStart", container(a)["lifecycle"])
        self.assertFalse(any(o["kind"] == "ConfigMap" for o in render({"policy": {"mode": "disabled"}})))
        self.assertEqual(container(a)["livenessProbe"]["exec"]["command"][-1], "")
        self.assertEqual(container(a)["readinessProbe"]["exec"]["command"][-1], revision)

    def test_external_immutable_policy_artifact(self):
        revision = "a" * 64
        objects = render({"policy": {"existingConfigMap": "retained-r1", "revision": revision}})
        self.assertFalse(any(o["kind"] == "ConfigMap" for o in objects))
        self.assertEqual(environment(objects)["HELM_POLICY_EXPECTED_REVISION"]["value"], revision)
        for policy in ({"existingConfigMap": "bad"}, {"existingConfigMap": "bad", "revision": "invalid"}, {"revision": revision}):
            render({"policy": policy}, valid=False)

    def test_policy_prevalidation_and_group_associations(self):
        import copy
        policy = json.loads(find(render(), "ConfigMap")["data"]["policy.json"])
        policy["groups"] = [{"name": "managed"}]
        policy["lists"]["blacklist"] = [{"domain": "BLOCKED.test", "groups": ["managed"]}]
        def normalize(value):
            return subprocess.run(["jq", "-ce", "-f", str(CHART / "files/policy.jq")], input=json.dumps(value), text=True, capture_output=True)
        result = normalize(policy)
        self.assertEqual(result.returncode, 0, result.stderr)
        domain = next(e for e in json.loads(result.stdout) if e["kind"] == "domains/deny/exact")
        self.assertEqual(domain["groups"], ["managed"])
        self.assertEqual(domain["key"], "blocked.test")
        for changes in ({"groups": [{"name": "Default"}]}, {"groups": [{"name": "duplicate"}, {"name": "duplicate"}]}, {"lists": {"blacklist": [{"domain": "one.test", "enabled": "false"}]}}):
            invalid = copy.deepcopy(policy)
            for key, value in changes.items():
                if key == "lists":
                    invalid[key].update(value)
                else:
                    invalid[key] = value
            self.assertNotEqual(normalize(invalid).returncode, 0)

    def test_pinned_list_validation_and_restore_owner(self):
        objects = render({"policy": {"owner": "original/release", "requirePinnedLists": True},
                          "pihole": {"lists": {"adlists": [{"address": "https://lists.test/v1.txt", "sha256": "a" * 64}]}}})
        policy = json.loads(find(objects, "ConfigMap")["data"]["policy.json"])
        self.assertEqual(policy["owner"], "original/release")
        self.assertEqual(environment(objects)["HELM_POLICY_OWNER"]["value"], "original/release")
        def normalize(value):
            return subprocess.run(["jq", "-ce", "-f", str(CHART / "files/policy.jq")], input=json.dumps(value), text=True, capture_output=True)
        result = normalize(policy)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(result.stdout)[0]
        self.assertEqual(entry["key"], "file:///etc/pihole/helm-snapshots/" + "a" * 64 + ".txt")
        for field, value in (("sha256", "invalid"), ("address", "http://lists.test/v1.txt")):
            changed = json.loads(json.dumps(policy))
            changed["lists"]["adlists"][0][field] = value
            self.assertNotEqual(normalize(changed).returncode, 0)
        policy["lists"]["adlists"][0].pop("sha256")
        self.assertNotEqual(normalize(policy).returncode, 0)
        policy["lists"]["adlists"][0]["enabled"] = False
        self.assertEqual(normalize(policy).returncode, 0)
        render({"policy": {"requirePinnedLists": True, "mode": "disabled"}}, valid=False)
        render({"policy": {"requirePinnedLists": True}, "command": ["custom"]}, valid=False)

    def test_probe_rejects_dns_error_with_successful_exit(self):
        command = container(render())["livenessProbe"]["exec"]["command"]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake = base / "dig"
            fake.write_text('#!/bin/sh\ncat "$DNS_PROBE_FIXTURE"\n')
            fake.chmod(0o755)
            fixture = base / "response"
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], DNS_PROBE_FIXTURE=str(fixture))
            for response, passed in ((";; status: SERVFAIL\n", False), (";; status: NOERROR\n", False), (";; status: NOERROR\npi.hole. 2 IN A 192.0.2.10\n", True)):
                fixture.write_text(response)
                result = subprocess.run(command, env=env, capture_output=True)
                self.assertEqual(result.returncode == 0, passed, result.stderr.decode())

    def test_service_account_and_independent_metrics(self):
        objects = render({"affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": []}}}, "metrics": {"enabled": True}, "serviceAccount": {"create": False}})
        for kind in ("StatefulSet", "Deployment"):
            spec = find(objects, kind)["spec"]["template"]["spec"]
            self.assertEqual(spec["serviceAccountName"], "default")
            self.assertFalse(spec["automountServiceAccountToken"])
        self.assertNotIn("affinity", find(objects, "Deployment")["spec"]["template"]["spec"])

    def test_functional_checks_reject_stale_or_missing_metrics(self):
        objects = render({"replicas": 2, "tests": {"authentication": False}, "metrics": {"enabled": True}})
        pod = find(objects, "Pod", "-test-functional")
        check = pod["spec"]["containers"][0]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "dig").write_text('#!/bin/sh\nprintf ";; status: NOERROR\\npi.hole. 2 IN A 192.0.2.10\\n"\n')
            (base / "curl").write_text('#!/bin/sh\ncat "$METRICS_FIXTURE"\n')
            for name in ("dig", "curl"):
                (base / name).chmod(0o755)
            fixture = base / "metrics"
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], METRICS_FIXTURE=str(fixture))
            env.update({v["name"]: v["value"] for v in check["env"]})
            targets = env["HELM_CHECK_TARGETS"].split(",")
            for values, passed in (([1, 1], True), ([1, 0], False), ([1], False)):
                fixture.write_text("".join(f'pihole_status{{hostname="{target}"}} {value}\n' for target, value in zip(targets, values)))
                result = subprocess.run(check["command"] + check["args"], env=env, capture_output=True)
                self.assertEqual(result.returncode == 0, passed)

    def test_monitoring_scope_and_unprivileged_synthetic_dns(self):
        objects = render({"replicas": 2, "metrics": {"enabled": True, "serviceMonitor": {"enabled": True}},
                          "monitoring": {"namespace": "observability", "labels": {"release": "stack"}, "alerts": {"enabled": True}, "syntheticDns": {"enabled": True}},
                          "networkPolicy": {"enabled": True}})
        monitor = find(objects, "ServiceMonitor")
        self.assertEqual(monitor["metadata"]["namespace"], "observability")
        self.assertEqual(monitor["metadata"]["labels"]["release"], "stack")
        self.assertEqual(monitor["spec"]["namespaceSelector"]["matchNames"], ["fixture"])
        rules = find(objects, "PrometheusRule")
        self.assertEqual(len(rules["spec"]["groups"][0]["rules"]), 4)
        self.assertEqual(rules["metadata"]["namespace"], "observability")
        job = find(objects, "CronJob")
        self.assertEqual(job["spec"]["concurrencyPolicy"], "Forbid")
        spec = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertNotIn("volumes", spec)
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        self.assertEqual(spec["containers"][0]["securityContext"]["capabilities"]["drop"], ["ALL"])

    def test_gitops_artifact_retention_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            values = base / "values.yaml"
            old, active, reference = (base / n for n in ("old.yaml", "active.yaml", "reference.yaml"))
            values.write_text(yaml.safe_dump({"pihole": {"web": {"existingSecret": "external"}}}))
            command = ["python3", str(ROOT / "scripts/policy-artifact.py"), "--release", "audit", "--namespace", "fixture", "-f", str(values), "--reference-output", str(reference)]
            subprocess.run(command + ["--output", str(old)], capture_output=True, check=True)
            values.write_text(yaml.safe_dump({"pihole": {"web": {"existingSecret": "external"}, "lists": {"blacklist": [{"domain": "blocked.test"}]}}}))
            subprocess.run(command + ["--retain", str(old), "--output", str(active)], capture_output=True, check=True)
            artifacts = list(yaml.safe_load_all(active.read_text()))
            self.assertEqual(len(artifacts), 2)
            selected = yaml.safe_load(reference.read_text())["policy"]
            self.assertIn(selected["existingConfigMap"], [o["metadata"]["name"] for o in artifacts])
            changed = yaml.safe_load(old.read_text())
            changed["data"]["policy.json"] += " "
            old.write_text(yaml.safe_dump(changed))
            result = subprocess.run(command + ["--retain", str(old), "--output", str(active)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_claim_templates_do_not_change_with_chart_or_image_version(self):
        import shutil
        old = find(render({"image": {"tag": json.loads((CHART / "files/compatibility.json").read_text())["dockerTag"]}}), "StatefulSet")["spec"]["volumeClaimTemplates"]
        with tempfile.TemporaryDirectory() as tmp:
            chart = Path(tmp) / "pihole"
            shutil.copytree(CHART, chart)
            metadata = yaml.safe_load((chart / "Chart.yaml").read_text())
            metadata.update(version="2.0.0-alpha.99", appVersion="2026.10.99")
            (chart / "Chart.yaml").write_text(yaml.safe_dump(metadata))
            new = find(render({"image": {"tag": json.loads((CHART / "files/compatibility.json").read_text())["dockerTag"]}}, chart=chart), "StatefulSet")["spec"]["volumeClaimTemplates"]
            self.assertEqual(old, new)


if __name__ == "__main__":
    unittest.main()
