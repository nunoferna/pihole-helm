# Staging deployment

Release and namespace: `pihole-staging`. Source: chart `2.0.0`, pinned Pi-hole Docker tag `2026.11.0`. The base staging configuration requires two eligible Linux nodes, Kubernetes 1.35 or later, suitable persistent storage and TCP/UDP DNS exposure. Homelab uses the local-storage and NodePort overrides described below.

Deployment status: **2.0.0 deployed and validated; retained staging is healthy** on 2026-11-15. Context `admin@homelab` uses `https://192.168.1.70:6443`. All three Talos nodes (`cp01`, `cp02`, `cp03`) are Ready, run Kubernetes 1.37.0 and have no scheduling taints. DNS replicas run on `cp01` and `cp03`; the exporter runs separately. Existing application DNS and router/client settings are unchanged.

Homelab has no default/dynamic StorageClass. Its existing database PVs remain dedicated to those applications. Staging has its own non-default `pihole-staging-local` StorageClass and two retained PVs, prebound to the StatefulSet's individual ordinal claims. `storage.yaml` records the exact mappings. Both PVs expose `/var/mnt/pihole-staging`, on different nodes, so the SQLite databases are independent.

`talos-volume.yaml` adds a directory-backed `UserVolumeConfig` to `cp01` (`192.168.1.73`) and `cp03` (`192.168.1.77`). It was applied in `no-reboot` mode. Talos reported both volumes and mounts ready; no partitioning, formatting or reboot was performed. This uses the existing EPHEMERAL filesystem mounted at `/var`: it is persistent host storage across pod replacements, not Kubernetes `emptyDir`. It has no independent filesystem or enforced 1Gi quota; PV capacity is a binding declaration. Node disk loss or resetting/wiping EPHEMERAL loses local data. Production still needs backup/restore proof and storage-capacity protection, preferably on a separately provisioned filesystem.

Local PV node affinity pins each ordinal to its original node. A failed node leaves that ordinal unavailable while the surviving replica serves DNS. Recover the node or deliberately rebuild/restore the affected ordinal; this does not provide automatic cross-node volume attachment or replicated SQLite. Retention protects against ordinary release/PVC lifecycle deletion, not disk loss. Do not delete/reuse retained PVs or their directories as routine cleanup.

The two Cilium LB pools contain only the already allocated addresses `192.168.1.10` and `192.168.1.11`, each restricted to an existing application. The homelab overlay uses automatically assigned NodePorts instead of allocating an unreserved LAN address. Kubernetes assigned `32052` for both UDP and TCP: test `192.168.1.73:32052` or `192.168.1.77:32052`. `externalTrafficPolicy: Local` means external tests must target nodes hosting a ready DNS replica. The network policy permits namespace-local clients and the verified workstation address `192.168.1.68/32`. External requests timed out under the initial namespace-only policy, then succeeded after that exact `/32` was allowed. Update the allowlist when the workstation address changes.

Cilium 1.20.2 L2 announcements do not support `externalTrafficPolicy: Local`; any future L2 load-balancer deployment must use a supported traffic policy and validate the resulting client identity behavior. See [Cilium's L2 documentation](https://docs.cilium.io/en/stable/network/l2-announcements/).

Prometheus restricts monitor discovery to namespace `monitoring`. `monitoring.yaml` installs a release-specific ServiceMonitor there, selecting the staging metrics Service across namespaces, plus replica-health, exporter-scrape, blocking-status and stale synthetic-success rules. These resources are now owned by the Helm release; `monitoring.yaml` contains only the homelab-specific Cilium source egress allowance. `values-homelab.yaml` allows only the matching Prometheus pods through the metrics ingress policy. Live verification confirmed one healthy exporter scrape target, active blocking-status samples for both DNS replicas, and all four alert rules loaded, evaluating successfully and inactive. Hubble initially identified a source-side Cilium egress denial; a separate staging-only rule now permits Prometheus to reach the exporter on TCP 9617 without changing the shared Flux-managed policy. Rules alone do not establish DNS availability or alert delivery: exporter samples may be stale, and independent external UDP/TCP DNS probing remains a production requirement. A chart-managed CronJob now checks the cluster Service and every replica over UDP/TCP every minute, including local answers, filtering and upstream resolution. A fresh scheduled success was verified after the final rollout. Its stale-success threshold is 180 seconds plus the 2-minute alert hold.

Preflight passed chart lint, all 23 chart regressions and `helm install --dry-run=server --hide-secret`. Helm's dry run does not establish schedulability, usable storage, Secret existence or data-plane connectivity.

The values file uses independent retained claims and a new DNS Service, private administration, automatic exporter authentication, a PDB and node anti-affinity. It defines reserved `.invalid` records for deterministic local-resolution and filtering checks. It does not change client/router DNS settings.

The admin Service remains private (`ClusterIP`); web ingress is namespace-local. Use an explicitly targeted local tunnel when needed:

```sh
kubectl --context admin@homelab -n pihole-staging \
  port-forward --address 127.0.0.1 svc/pihole-staging-web 8080:80
```

Then open `http://127.0.0.1:8080/admin/`. Credentials are held in a versioned immutable Kubernetes Secret referenced by `values.yaml`, key `password`. Do not copy them into Git, shell arguments or reports.

Before installation:

1. Confirm the context, cluster version, eligible nodes, storage and available load-balancer addresses.
2. Verify that the staging release/namespace do not conflict with an existing installation.
3. Create a generated credential in the versioned Secret selected by `values.yaml` (currently `pihole-staging-web-v2`), key `password`, without logging it or storing it in Git. Do not include trailing whitespace.
4. Render/review the chart and perform a server-side dry run against the selected cluster.
5. Install with explicit context/namespace and wait for both replicas and metrics.

```sh
helm lint charts/pihole -f deploy/staging/values.yaml
helm template pihole-staging charts/pihole \
  --namespace pihole-staging -f deploy/staging/values.yaml
```

For homelab, append `-f deploy/staging/values-homelab.yaml` to lint, render and install commands. The dedicated storage prerequisites are already provisioned. The release is managed manually for this staging exercise; incorporate the supplied Talos patch, storage, Helm values and monitoring resources into the infrastructure source of truth before relying on GitOps reconciliation.

`scripts/validation/homelab-staging.py` targets only this release and context, creates a disposable client on `cp02` and replaces each DNS pod sequentially. The completed deployment validation also rotated v1 credentials to a retained versioned v2 Secret, authenticated both replicas, and checked DNS/exporter behavior afterward. Repeating that one-time rotation requires `--rotate-credentials` and a v1 deployment; ordinary reruns keep the current credential. Use `--output /tmp/pihole-staging-rerun.json` to preserve the original evidence. It leaves staging running and writes credential-free evidence to `docs/validation/homelab-staging.json`. The initial run is retained separately: it failed on an empty test marker without a pre-restart read-back. The corrected run verifies and syncs the test write before deleting a pod. Do not infer database data loss or a diagnosed transport bug from the initial marker assertion alone.

Validation must establish UDP/TCP resolution of `staging-health.invalid` to `192.0.2.42`, blocking of `staging-blocked.invalid`, upstream resolution, per-pod readiness on different nodes, correct Service/PDB selection, persistence after pod replacement, Secret rotation and fresh authenticated exporter status. Retain the staging release for subsequent policy work after these checks; remove only disposable validation jobs/pods.

Production cutover, node/VIP failure certification, controlled list snapshots, independent external probing, notification delivery and backup restoration remain separate gates. Owned reconciliation, immutable revisions and scheduled in-cluster checks are implemented and exercised. See [MIGRATION.md](../../charts/pihole/MIGRATION.md).

## Alpha.2 migration and evidence

The alpha.1 upgrade revealed changing chart/app-version labels in immutable StatefulSet claim templates. Alpha.2 uses stable labels. The one-time migration first verified two ready replicas, Retain policies, unchanged storage specs and valid per-replica consistent gravity backups. It orphaned only the controller, verified running pod and PVC UIDs were unchanged, then recreated the controller with Helm. Both original PVCs remained bound. A subsequent ordinary upgrade succeeded without force flags or controller recreation. The explicit adoption rollout claimed only the existing staging test rule; adoption is now disabled, and both journals/readiness markers match the selected immutable revision.

The pre-migration databases remain under `/etc/pihole/helm-migration-backup/gravity-before-alpha2.db` on each PVC and passed SQLite integrity checks. These same-disk backups are a migration safeguard, not an off-node backup or a demonstrated disaster restore. The legacy ConfigMap and immutable alpha.2 artifacts remain retained; do not garbage-collect references within the rollback window.

The existing ServiceMonitor and PrometheusRule were explicitly transferred into this Helm release. Helm 4 field ownership needed a one-time conflict resolution for those staging monitoring resources; the final ordinary upgrade required none. The existing Flux-managed Prometheus Cilium policy was left intact.

See [controller/storage migration evidence](../../docs/validation/homelab-alpha2-migration.json), [five final staging checks](../../docs/validation/homelab-alpha2-runtime.json), [eleven isolated lifecycle cases](../../docs/validation/policy-runtime.json), and the [policy lifecycle guide](../../docs/policy-lifecycle.md). The strengthened `helm test` passed on staging; its successful hook pod was automatically removed. Scheduled job history is bounded by the CronJob.

## Alpha.3 dependency refresh (historical)

This refresh used chart `2.0.0-alpha.3`, Pi-hole `2026.11.0` (FTL 6.9, Core 6.6, Web 6.6.1), exporter `v1.3.1` and the Helm `4.3.2` CLI. An ordinary Helm upgrade preserved the StatefulSet controller UID, both original PVC UIDs, ownership identities and policy revision; adoption stayed disabled. No controller recreation or forced field ownership was needed.

Thirteen isolated cases passed first, including a retained-volume container upgrade from `2026.10.0` and mixed-version exporter authentication. Six staging checks then passed: consistent same-disk migration backups, retained-state image upgrade, strengthened Helm tests, workstation UDP/TCP filtering, a fresh scheduled probe success and exporter/alert-rule evaluation. See [latest fixture results](../../docs/validation/latest-policy-runtime.json), [staging upgrade results](../../docs/validation/latest-dependency-upgrade.json) and [upstream/digest review](../../docs/validation/latest-upstream-review.json). Prior alpha.2 reports remain historical evidence.

The latest backup path is recorded in the staging upgrade report. Each replica has a SQLite-consistent gravity database backup, ownership journal and configuration copy with private permissions on its own PVC. This is a same-disk migration safeguard; off-node restoration remains untested.

## Stable 2.0.0 retained upgrade

The retained staging release is now `pihole-2.0.0`, revision 8, with both DNS replicas Ready. The exact tested canonical archive passed six [upgrade/runtime checks](../../docs/validation/stable-staging-upgrade.json); the controller, original claims and ownership identities were preserved without adoption or controller recreation. Migration backups remain private on each existing PVC. The generic chart defaults to private DNS, while this overlay explicitly preserves the workstation-restricted NodePort profile.

DNS egress is limited to CoreDNS, the configured recursive upstreams and HTTPS; exporter egress is limited to CoreDNS and release DNS APIs. Same-node controls demonstrated that HTTP port 80 works outside the selector and is denied for both DNS pods. This profile's broad HTTPS allowance supports the stock update-checker/factory feeds; a production controlled mirror can narrow it further using fixed destinations or a CNI FQDN policy. Shared Flux/Prometheus policy was not changed.

The chart remains portable: these node addresses, local-volume choices and Cilium rules are deployment-only configuration. Generic kind fixtures separately prove controlled snapshots, external VIP node failure, TLS and encrypted fresh-namespace restore. Staging's directory-backed local PVs still share `/var` and have no enforced 1Gi quota; no physical disk/node/site recovery or notification delivery is certified here.

Future staging promotion requires an exact tested archive:

```sh
python3 scripts/validation/dependency-upgrade.py --promote-staging \
  --fixture-report docs/validation/stable-arm64-policy-final.json \
  --chart /path/to/tested/pihole-2.0.0.tgz \
  --output /tmp/staging-upgrade.json
```

The helper checks the report's archive digest before changing the release. Keep the original desired values and explicit 1Gi claim-template size; do not reuse legacy Service values.
