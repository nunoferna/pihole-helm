# Security release gate

Run `scripts/validation/security-gate.py --reports /tmp/pihole-security --output /tmp/pihole-security-summary.json` with Helm, PyYAML and Trivy 0.76.2. The gate renders real manifests with Helm, requires a nonempty Kubernetes scan, then scans the pinned Pi-hole and exporter images separately for amd64 and arm64. HIGH and CRITICAL image vulnerabilities fail the gate, including unfixed vulnerabilities. Scanner/download failures also fail it. The raw reports remain in the chosen reports directory.

Trivy's embedded Helm renderer cannot evaluate this chart's compatibility file reliably and can report success after skipping the chart. This is why the gate scans Helm-rendered Kubernetes YAML and checks that resources were inspected.

Two manifest findings have narrowly scoped exceptions in the gate:

| Finding | Accepted scope and reason |
| --- | --- |
| KSV014 | Only the `pihole` container in the rendered DNS StatefulSet. The pinned stock image requires writable root files during startup, cron installation and privilege setup. Prior runtime experiments rejected read-only-root startup. Exporter, tests and scheduled DNS checks must retain read-only roots. |
| AVD-KSV-0109 | Only the exact policy ConfigMap containing the reviewed `import-lists.sh`, `policy.jq` and policy JSON. The flagged text is the shell runner's jq `{password:...}` expression, which reads the local CLI credential at runtime; it contains no credential. The gate checks both exact source files, allowed data keys, artifact identity and the complete scanner message. Extra secret keys or other findings fail. |

The default image retains a root startup process and selected capabilities before running FTL as the Pi-hole user. This chart does not claim Kubernetes Restricted Pod Security compatibility. Keep administration private, use versioned external Secrets, restrict clients with NetworkPolicy and configure upstream/list-mirror egress for your CNI. No ServiceAccount token is mounted by default. Native and chart logs never intentionally print passwords.

The security result is time-sensitive. Repeat these scans at promotion and on a schedule; pinning an image does not freeze its vulnerability status. A future finding needs remediation or a separately reviewed, expiring exception before release.
