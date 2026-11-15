# Pi-hole Helm Chart

A Helm chart for Pi-hole v6 on Kubernetes. The current source is **v2.0.0**, targeting a reproducible DNS deployment with independent storage, controlled policy changes and tested recovery. See the [runtime evidence and support limits](docs/validation/README.md) and [production architecture](docs/production-architecture-v2.md). Publication requires the complete CI gate for the exact package; a version in this checkout does not mean it has been published.

## Install

Use Kubernetes 1.35–1.37 with a compatible storage and TCP/UDP load-balancer implementation. Create credentials in the intended namespace, then install from this checkout:

```sh
kubectl create namespace pihole
kubectl -n pihole create secret generic pihole-web-v1 \
  --from-file=password=/path/to/password-file
helm upgrade --install pihole ./charts/pihole --namespace pihole \
  --set pihole.web.existingSecret=pihole-web-v1
```

DNS defaults to a private ClusterIP. Select `services.dns.type: LoadBalancer` or `NodePort` explicitly for client access, and configure the provider and client restrictions. Load-balancer classes and source ranges are supported for DNS/web Services. The password file must not end in whitespace/newlines when shared with the exporter. No password is generated automatically. For a disposable test, an explicit `pihole.web.password` creates a deterministic chart-managed Secret. Prefer externally managed, versioned Secrets for production.

Read the [migration guide](charts/pihole/MIGRATION.md) before moving an existing v1 installation. Use a parallel release with separate claims and a controlled DNS-address cutover.

## Workload and availability

Every replica count uses a StatefulSet. Each instance has its own retained PVC and stable identity. DNS Services select only DNS pods; metrics and Helm test pods cannot contribute to DNS endpoints or the disruption budget.

Internal listener ports default to DNS 5353, HTTP 8080 and HTTPS 8443, with Services exposing 53/80/443. DNS listening mode defaults to `ALL` for cross-pod Service traffic; restrict access with private addressing and NetworkPolicy. Configure `listeners` to change internal ports, and set `listeners.https: 0` to disable HTTPS. Named ports, FTL environment, probes, discovery, network policy and exporter targets follow the same listener settings.

Use two replicas on two nodes for redundancy, or three on three nodes for maintenance headroom. [examples/values-ha.yaml](examples/values-ha.yaml) provides a portable HA starting profile with node anti-affinity, a PDB and client restrictions. Edit its release-name label, CIDRs and upstreams for your environment. A load balancer must support TCP/UDP and healthy-node failover. MetalLB layer 2 with `externalTrafficPolicy: Local` sends traffic only to pods on the announcing node; it does not distribute external queries across all replicas.

Caches, history, sessions and DHCP leases remain per-instance. DHCP with multiple replicas is rejected. The default DNS profile disables DHCP and NTP functionality. The stock image starts with root-owned setup; non-root/Restricted compatibility is not claimed.

## Configuration and policy

Native configuration remains under `pihole.ftl`. Null values are omitted; configured false, zero and empty values are emitted. Kubernetes Service settings are separate under `services.dns`, `.web`, `.dhcp`, `.dhcpv6` and `.ntp`; legacy locations are rejected. `pihole.ftl.dhcp.ipv6` is a native boolean. Use `scripts/migrate-values.py` and review its output when moving older values. `files.pid` is rejected for FTL v6.8. Conflicting listener overrides are rejected so networking and probes stay consistent.

The chart ships no sample allow/deny rules or groups. Fresh-image factory adlists and bootstrap behavior still come from Pi-hole. Declare upstream DNS explicitly for your environment and avoid a CoreDNS-to-Pi-hole forwarding cycle. `networking.dnsPolicy`, `dnsConfig` and `hostAliases` support bootstrap configuration; the image's gravity initialization checks public hostname resolution even with empty adlists.

A bounded local API reconciler manages journaled records, safely removes owned entries deleted from values, and preserves unmanaged data. Policy artifacts are immutable, content-named and retained for rollback; readiness verifies the expected revision. Existing records require explicit adoption. See the [policy lifecycle guide](docs/policy-lifecycle.md) for adoption, partial-failure recovery, externally retained GitOps artifacts and rollback limits. Production deployments should set `policy.requirePinnedLists: true` and pin every enabled subscription with `sha256`. Verified bodies are cached on each PVC and reused during provider outages. Scheduled checks share the runner lock, preserve readiness for unchanged verified policy, and avoid needless gravity rebuilds. See [encrypted recovery](docs/recovery.md).

Chart-managed credential changes roll DNS and exporter pods. For an existing Secret, use a versioned name or change `credentialsRevision` to roll consumers after rotation. `skipPasswordEnv` supports external injection; supply exporter authentication separately in that mode.

## Monitoring and access

Metrics run in a separate optional Deployment and automatically authenticate with the configured web Secret. Set `metrics.enabled: true`. Prometheus Operator integration and Grafana dashboard resources remain optional; see [values-monitoring.yaml](examples/values-monitoring.yaml). Exporter scheduling and annotations are independent of DNS pods.

Exporter HTTP health does not prove functioning DNS or fresh target statistics. `helm test` verifies per-replica/Service UDP/TCP answers, configured filtering, credentials and blocking-status samples. Enable `monitoring.syntheticDns` for scheduled DNS checks and `monitoring.alerts` for stale-success/replica/exporter/blocking alerts; use `monitoring.namespace` and labels for discovery. Independent external DNS-address probes are still required. `INTERVAL` and `PIHOLE_API_TOKEN` are not supported by exporter v1.3.1; configure scrape intervals in Prometheus.

Administration is private by default through a ClusterIP Service pinned to pod-0. Each pod also has a headless discovery address. A shared UI does not aggregate history or replicate session/policy changes.

When NetworkPolicy is enabled, `dnsAllowFrom` controls clients; empty web/metrics allowlists deny general ingress; `networkPolicy.allowTests` separately permits same-release labelled test/probe pods. Optional `networkPolicy.egressEnabled` applies explicit `egress` rules to DNS pods and `metricsEgress` rules to the exporter; empty rules deny outbound traffic. Declare bootstrap DNS, upstreams, list mirrors and monitoring paths. Use CNI-specific policy for hostname-based destinations. Validate source-IP preservation where client-specific policy is used; the homelab overlay is a separate deployment example.

## Development

```sh
helm lint charts/pihole -f charts/pihole/ci/default-values.yaml
python3 -m unittest discover -s scripts/validation -p 'test_*.py' -v
ct lint --config .github/configs/ct.yaml --charts charts/pihole
```

The CI credential file is a disposable test fixture. CI builds one reproducible package, tests that package on Kubernetes 1.35/1.36/1.37 with Helm 3/4 on amd64/arm64, runs blocking image/manifest scans and performs disposable VIP-failure and encrypted-restore tests. The manually invoked release workflow signs and publishes the same bytes, independently resuming GitHub, OCI and Pages destinations. See [release operations](docs/releasing.md). Runtime harnesses under `scripts/validation` use explicitly guarded isolated fixtures; homelab harnesses always select their context and namespace. Read each script before running it: some replace staging pods, and credential rotation requires explicit selection.

- [Generated values reference](charts/pihole/README.md)
- [Migration and behavior changes](charts/pihole/MIGRATION.md)
- [Architecture and deployment acceptance](docs/production-architecture-v2.md)
- [Runtime validation](docs/validation/README.md)
- [Encrypted backup and restore](docs/recovery.md)
- [Security gate and scoped exceptions](docs/security.md)
- [Release operations](docs/releasing.md)
- [Contributing](CONTRIBUTING.md)

Apache-2.0; see [LICENSE](LICENSE).
