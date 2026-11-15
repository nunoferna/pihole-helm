# Migrating to v2

Version `2.0.0` targets Pi-hole `2026.11.0` (FTL 6.9, Core 6.6, Web 6.6.1) and exporter `v1.3.1`. It separates native settings from Kubernetes Services, adds verified local subscription snapshots, encrypted recovery and stricter release/security gates. Read the [validation evidence and deployment limits](../../docs/validation/README.md). Load-balancer, CNI, storage, client and site availability still require acceptance in your own environment.

## Existing alpha.1 installations

Back up/export first. Retain the old ConfigMap and adopt only reviewed desired records with `policy.adoptExisting: true`; after all replicas are healthy, disable adoption and roll again. The old additive runner did not preserve ownership claims, so a downgrade across this boundary is not a supported database rollback. Retained alpha.2 policy artifacts support rollback within the new lifecycle model. See the [detailed procedure](../../docs/policy-lifecycle.md).

Alpha.1 also embedded changing chart/app-version labels in immutable claim templates. Alpha.2 removes those labels so future version upgrades keep the templates stable. Existing alpha.1 StatefulSets need a one-time guarded controller recreation: confirm all PVCs are Bound, retention is Retain and storage requirements/selectors are unchanged; back up data; orphan the StatefulSet controller (`kubectl delete statefulset NAME --cascade=orphan`); verify running pod and claim UIDs are unchanged; then recreate it with Helm. Keep exact context/namespace arguments. Do not delete pods/PVCs or use Helm force-replace as a substitute. This was exercised on retained staging, followed by an ordinary second upgrade without recreation. StorageClass/access-mode/size changes require their own migration.

## Existing v1 installations

Use a parallel v2 release with a separate DNS address and separate claims. An ordinary in-place upgrade is not supported: workload selectors changed, every replica count now uses a StatefulSet, and single-replica v1 claims have different names.

1. Export policy using the application-supported interface and take a consistent backup of the old installation. Keep the original claims and release intact during validation.
2. Create versioned credentials in the new namespace and set `pihole.web.existingSecret`. Use the same password across replicas unless custom exporter credentials are explicitly configured.
3. Install v2 under a new release/namespace, with distinct DNS addressing. Do not let both releases request the same load-balancer IP.
4. Restore/adopt policy deliberately. Configure all rules intended for import, and validate every instance over UDP and TCP. Enable `policy.adoptExisting` only for a reviewed adoption rollout, then disable it. Removed desired records are deleted only when ownership is established. See the [policy lifecycle guide](../../docs/policy-lifecycle.md).
5. Validate client-specific filtering, source-IP preservation, administration and monitoring. Then move client DNS routing to the new installation with a provider-specific cutover procedure.
6. Retain the old installation and backups for your rollback window. Reverting client routing is the rollback path. Application database migrations are not undone by `helm rollback`.

Never attach one writable Pi-hole database to multiple instances, or copy live SQLite databases without a consistency procedure.

## Values and behavior changes

| Area | v2 behavior |
| --- | --- |
| Workload | StatefulSet at every replica count; `strategy` is rejected. Use `updateStrategy`. |
| Selectors | DNS pods carry `app.kubernetes.io/component: dns`; exporter and test pods are excluded from DNS/PDB selection. |
| Credentials | Provide an existing Secret, a non-empty explicit password, or external injection. No random password generation or password printing in NOTES. |
| Rotation | Chart-managed password changes roll DNS/exporter pods. Change `credentialsRevision` when rotating an existing Secret. Versioned Secret names are preferable for controlled rollouts. |
| Ports | Use `listeners.dns`, `.http`, `.https`. Defaults are 5353/8080/8443 internally; Services retain 53/80/443. Set HTTPS to 0 to disable it. Conflicting native FTL/extraEnv listener overrides are rejected. |
| FTL leaves | Null is omitted. False, zero, empty strings and empty arrays are emitted, including clearing persisted configuration. Review explicitly configured empty values. |
| Image compatibility | Tag/digest pairs are checked against `files/compatibility.json`. Custom images require an explicit `image.allowUnverified` (or exporter equivalent) and new validation. |
| Native FTL surface | `files.pid` is rejected for FTL v6.8. Kubernetes helpers are separate under `services.*`; DHCPv6 is the native `pihole.ftl.dhcp.ipv6` boolean. Managed gravity must remain on the per-PVC `/etc/pihole/gravity.db` path. |
| Default policy | Sample allow/deny rules and sample groups are removed. Pi-hole's own factory adlist/bootstrap behavior still applies on fresh storage. |
| Policy import | Structured JSON, per-PVC ownership journals and bounded local API reconciliation. Owned removals are applied even for an empty desired policy. Unmanaged collisions require explicit adoption; Default and unmanaged group dependencies are protected. |
| DNS health | Startup, readiness and liveness verify a local DNS response. Configure `probes.dns` if `pi.hole` is not suitable for your configuration. Startup/readiness additionally require the exact applied policy revision; liveness is independent of policy status. |
| Discovery | Headless discovery publishes DNS-pod addresses even before readiness for diagnosis/metrics. Client Services retain Ready-only routing. |
| Storage | Independent per-pod claims; retained on controller deletion/scale-down by default. Persistence disabled uses `emptyDir`. Explicit `storageClassName: ""` is preserved. |
| ServiceAccount | Release-scoped name by default, token mounting disabled. `create: false` and an empty name use the namespace default account. |
| Optional functions | DHCP, NTP servers/time sync and elevated nice priority are disabled in the default DNS profile. Multi-replica DHCP is rejected. |
| Metrics | Separate scheduling/annotations, pinned image, automatic web-Secret authentication, native health endpoints. `INTERVAL` and `PIHOLE_API_TOKEN` are obsolete. |
| NetworkPolicy | Ports follow listeners. Empty web/metrics allowlists deny general ingress; allowTests separately permits labelled validation pods; DNS client restrictions are configured separately. Optional egress is explicit and role-scoped; configure DNS and exporter destination rules for your environment. |
| Kubernetes | Supported minors 1.35–1.37; CI gates Helm 3/4 and amd64/arm64. Exact locally tested combinations and CI execution limits are recorded in the validation reports. |

Native FTL settings are under `pihole.ftl`; Kubernetes Service settings are under the root `services` tree. Legacy Service helpers are rejected. Use the values-only migration below and review its output.

## Storage maintenance

Replica changes preserve the StatefulSet and claim naming. StorageClass, access mode and claim-template changes are not ordinary Helm updates. Expand existing PVCs only with a supporting StorageClass and a tested procedure; use controlled controller recreation or a parallel release for immutable-template/storage migrations. Retention does not replace backups.

## External injection and metrics

With `skipPasswordEnv: true`, supply the Pi-hole credential through `extraEnv` or your injector, and supply exporter credentials independently through `metrics.env`. Automatic exporter authentication is disabled in this mode. An application password with reduced privileges is not a read-only API role.

Automatic exporter authentication uses a mounted Secret file, preserving commas in a shared password. Exporter v1.3.1 trims trailing whitespace from password files; credentials used by both applications must not end in whitespace. Secret-file rotation still requires an exporter rollout.

The exporter may return old samples when a target fails. Use external UDP/TCP DNS probes for availability and do not interpret Prometheus `up` as policy freshness or working DNS.

## Validation and monitoring

DNS listening mode now defaults to ALL for Kubernetes cross-pod traffic; explicitly set null only if you intend to inherit image/persisted behavior. Restrict exposed DNS clients with private addressing and NetworkPolicy. The functional Helm test replaces HTTP-only and public-name-only checks. Injected credentials need tests.existingSecret or tests.authentication=false. Scheduled synthetic checks are opt-in, use no credentials, and alert on stale/missing successes when monitoring alerts are enabled. They cover cluster paths; external VIP, notification delivery and site failure remain separate gates.

## Moving from alpha.2 to alpha.3

This is an ordinary image/tooling upgrade with stable workload selectors and claim templates; no controller recreation or fresh adoption is required. Keep policy.adoptExisting=false. Take consistent pre-upgrade backups and verify the retained ownership journal and DNS filtering afterward. The previous-image fixture exercises retained data across a container restart before staging promotion. Database restoration, rather than a plain Helm downgrade, remains the recovery boundary if a future application migration is incompatible.

## Moving from alpha.3 to 2.0.0

Back up first. Move Kubernetes Service settings to the root tree:

| Old location | New location |
| --- | --- |
| `pihole.ftl.dns.service` | `services.dns` |
| `pihole.web.service` | `services.web` |
| `pihole.ftl.dhcp.service` | `services.dhcp` |
| `pihole.ftl.dhcp.ipv6.service` | `services.dhcpv6` |
| `pihole.ftl.ntp.service` | `services.ntp` |
| `pihole.ftl.dhcp.ipv6.active` | `pihole.ftl.dhcp.ipv6` |

```sh
python3 scripts/migrate-values.py old-values.yaml > migrated-values.yaml
```

The tool never overwrites the input or touches a cluster. It preserves values, drops YAML comments and refuses conflicts between legacy and new locations. Review every values layer. This is a values conversion, not an in-place v1 storage/workload migration. Avoid `--reuse-values`, which reintroduces removed helper keys.

The default claim is now 1Gi and DNS limits are 512Mi/1 CPU. Preserve an existing smaller claim-template size explicitly if you are not performing a separately planned expansion; immutable claim-template changes cannot be applied by an ordinary upgrade. The supplied staging overlay already declares 1Gi. Startup now drops ALL capabilities and adds the stock image's ownership/identity/capability/shutdown set; low-port capabilities are unnecessary for default listeners. DHCP-specific capabilities remain conditional. Kubernetes support is bounded to minors 1.35–1.37.

Enable pinned-list mode deliberately. Existing enabled unmanaged/factory feeds must first be disabled or migrated under reviewed policy; new fresh pinned installations start with an empty initial subscription file. The new runner avoids rebuilding unchanged lists and serializes scheduled refresh through the same lock. Preserve old policy artifacts and snapshot bodies during the rollback window. Backup/restore now includes verified content and the ownership journal; original credentials, TLS Secret sources and Helm values remain independently retained.

The former optional UDP `nc -z` DHCP/NTP tests have been removed because they did not establish protocol functionality. Those auxiliary functions are outside the certified DNS HA profile. The chart does not supply lease failover or time-service certification.

DNS Services now default to private ClusterIP rather than creating an external LoadBalancer automatically. Preserve `services.dns.type` explicitly when upgrading an exposed installation. Optional load-balancer class/source ranges are scoped to LoadBalancer Services. Optional egress controls are separate for DNS and metrics; enable them only with reviewed bootstrap/upstream/mirror/API rules.
