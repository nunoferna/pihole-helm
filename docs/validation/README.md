# Runtime validation of the proposed v2 architecture

Executed on 2026-10-28 against the image digests recorded in the [architecture plan](../production-architecture-v2.md). These are bounded compatibility experiments, not production HA certification. The original experiments used the reviewed v1 chart. A later implementation milestone is recorded below.

The environment was Docker 28.4.0, linux/amd64, in Colima, plus a dedicated one-node kind v0.33.0 / Kubernetes v1.37.0 cluster. The Kubernetes test used the amd64 child manifest `sha256:d8d848307ffe7f8c4b42be423b33295d6a45282215eb5a96fe25e05fa39b1abc` of the pinned Pi-hole multi-platform index.

## Results and architectural consequences

| Experiment | Observed result | Consequence |
| --- | --- | --- |
| Stock entrypoint with fresh storage | Local DNS became usable with a controlled bootstrap fixture | Stock entrypoint remains a viable baseline |
| Docker no-new-privileges and current chart `allowPrivilegeEscalation: false` | DNS worked in the tested Docker/kind environments | This setting can be retained, subject to the port-binding condition below |
| Explicit reduced startup capability candidate | Local DNS worked with and without no-new-privileges | Candidate for further testing; not proof of a minimal capability set or all-feature compatibility |
| Non-root startup | Exited with permission/capability setup failures | Do not advertise stock-image Restricted/non-root support |
| Read-only root with selected writable tmpfs paths | Exited while modifying root-owned files and applying FTL file capabilities | This mount set is insufficient; a hardened runtime needs separate work |
| No-new-privileges, port 53, unprivileged-port threshold 1024 | DNS socket creation failed with permission denied | Port-53 success depends on runtime configuration |
| Same threshold and security setting, internal port 5353 | Local DNS became usable and remained responsive | Use 5353/8080 internally in the proposed production profile; implement consistent Service/probe/metrics mappings |
| Empty adlists, empty storage, no bootstrap name resolution | No DNS service within the 90-second test budget; gravity waited for name resolution | Empty policy does not remove the image's bootstrap resolver dependency |
| API authentication and policy mutation | Admin and local CLI authentication worked; groups, clients, exact domain rules, and block subscriptions supported create/update/delete | A local API apply runner is feasible without shell/SQL interpolation |
| JSON text and group membership | Quotes, dollar signs, and backticks round-tripped as data; explicit client membership excluded default group 0; group 0 remained present | Structured API input and explicit group handling are appropriate |
| Controlled local DNS policy | Expected local and allowed answers plus blocked `0.0.0.0` answers over UDP and TCP | Basic filtering worked with the pinned image and fixture |
| Persisted restart after stopping list provider | Existing blocking and local DNS recovered without the provider | Preserve usable stored policy during same-revision recovery |
| Exporter v1.3.0 with two authenticated targets | Both targets produced samples | Existing exporter version supports this Pi-hole release |
| One exporter target stopped | `/metrics` still returned 200 and retained samples for the down target; scraping recovered after target restart | Exporter reachability is insufficient as DNS-health/freshness evidence |
| Current chart, two replicas on one kind node | Both pods served expected local answers over UDP and TCP | Basic startup works with the documented fixture overrides; this does not prove node-failure HA |
| Unrelated Ready pods with chart metrics/test labels | Headless EndpointSlices included both unrelated pods; PDB counted 4 healthy pods and allowed 2 disruptions although only 2 pods served DNS | Component selectors are a confirmed release-blocking defect |
| Bounded `postStart`, authenticated loopback API, startup/readiness probes | Prototype reached Ready with zero restarts | The proposed local bootstrap order is feasible; complete policy application/rollback still needs implementation |

The security cases used fresh named volumes, preseeded empty `adlists.list`, no network, and no published ports. Positive cases added static TEST-NET `/etc/hosts` entries for the image's `raw.githubusercontent.com`/`github.com` resolution check. This deliberately isolates application startup from public DNS; it is a test fixture, not a production recommendation. The negative bootstrap experiment omitted those entries.

The stock/no-new-privileges cases showed FTL running as UID/GID 1000. Under no-new-privileges it had no effective capabilities. The kind pods reported `net.ipv4.ip_unprivileged_port_start=0`; the additional threshold-1024 experiment establishes why that environment detail matters. Higher internal ports do not eliminate the entrypoint's root/capability setup requirements.

The API experiment used an internal Docker network and a local HTTP blocklist server. It preseeded only a fixture URL, generated temporary credentials, and tested actual DNS outcomes. It did not exercise every API endpoint, every FTL value, atomic multi-endpoint updates, rollback, or a finished reconciliation implementation.

The chart experiment retained current templates and used test values: two replicas, ClusterIP DNS, empty policy inputs, explicit upstream/local host settings, NTP disabled, a seeding init container, and a 512Mi container memory limit. A post-render hostAliases fixture satisfied the bootstrap check. Metrics/test selector leakage was reproduced using harmless sleeping pods with the chart's actual label combinations, not live exporter/test workloads.

The first lifecycle fixture incorrectly used unauthenticated `GET /api/auth` with `curl --fail`; the API correctly returned 401 and the hook failed. That failed experiment remains in `kubernetes-runtime.json`. The corrected fixture authenticates using `/etc/pihole/cli_pw`, logs out, then marks startup complete; its separate passing rerun is in `lifecycle-runtime.json`. This was a test-fixture correction, not an application change. Initial namespace deletion exceeded its budget; final cleanup is recorded separately after deleting the dedicated kind cluster.

## Evidence files

- [Security matrix](security-runtime.json): six cases, including the observed rootless/read-only failures.
- [Missing bootstrap DNS](security-no-bootstrap-dns.json): bounded negative startup experiment.
- [Privileged-port failure](security-privileged-ports.json) and [high-port success](security-high-port.json).
- [API, DNS, persistence, and exporter results](api-runtime.json): ten passing checks.
- [Current chart and selector reproduction](kubernetes-runtime.json), including the initial failed lifecycle fixture.
- [Corrected authenticated lifecycle](lifecycle-runtime.json).
- [Final cleanup](cleanup.json).

Timestamps, image references, timings, and diagnostic output are recorded in the JSON files. Startup timings are observations from this small fixture and are not capacity or availability targets. Only generated test credentials were used; reports omit or redact them.

## Reproducing the experiments

The scripts require Python 3 and Docker. The Kubernetes harness additionally needs PyYAML, Helm, kubectl, the exact dedicated cluster/context, and its separate kubeconfig. Scripts create uniquely named containers/volumes or their fixed test namespace and clean up those resources. They do not deploy to the default Kubernetes context. Review available VM resources before running cases concurrently.

```sh
python3 scripts/validation/security-runtime.py
python3 scripts/validation/security-runtime.py \
  --cases stock --no-bootstrap-dns --startup-timeout 90 \
  --output /tmp/security-no-bootstrap-dns.json
python3 scripts/validation/security-runtime.py \
  --cases no_new_privileges_privileged_ports --dns-port 5353 \
  --output /tmp/security-high-port.json
python3 scripts/validation/api-runtime.py
```

The security script treats the specified negative cases as expected outcomes. The API script returns failure for failed assertions or cleanup errors. Required Docker images must already exist locally or be pullable; the API fixture uses the pinned official Python image recorded in its report.

The isolated Kubernetes cluster was created with:

```sh
kind create cluster --name pihole-audit-k8s \
  --kubeconfig /tmp/pihole-audit-k8s.kubeconfig \
  --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5

python3 scripts/validation/kubernetes-runtime.py
python3 scripts/validation/kubernetes-runtime.py --only-lifecycle \
  --namespace pihole-audit-lifecycle --output /tmp/lifecycle-runtime.json

kind delete cluster --name pihole-audit-k8s \
  --kubeconfig /tmp/pihole-audit-k8s.kubeconfig
```

The tested kind binary was v0.33.0 for the macOS arm64 host; its downloaded checksum was `0c8c7dbe5e23594a198b786c4bc13dacc101fa6196b0cb0b23a1ca44e61f4b4f`. The Docker VM and tested containers were amd64. Images can be pulled inside the cluster. Importing an amd64-only local copy of a multi-platform image requires platform-aware import; the harness deliberately uses the verified amd64 manifest.

## Remaining release gates

The v2 alpha implements the correctness foundation and an additive API importer; the complete production policy runner remains unfinished. Remaining validation includes arm64, the full supported Kubernetes/Helm matrix, realistic list/history/QPS capacity, TLS/admin access, controlled list snapshots, interrupted policy application, real upgrades/rollback, backup restoration, and production storage behavior. The homelab checks below exercise real Cilium policies, credential rotation and multi-node pod recovery. They do not establish abrupt node/zone failure or load-balancer failover guarantees.

## v2 alpha implementation regression

The implementation is versioned `2.0.0-alpha.1`. [v2-runtime.json](v2-runtime.json) records a separate isolated kind test of the actual Helm chart, using a privileged-port threshold of 1024. It checks Service port 53 routing to internal port 5353 over UDP/TCP, bounded JSON/API policy import, scaling without replacing the StatefulSet, isolation of headless/PDB selection from unrelated pods, automatic exporter Secret-file authentication, active blocking status for both targets, and credential rotation with a comma-containing password.

The chart contract suite runs with `python3 -m unittest discover -s scripts/validation -p test_chart.py -v`. It covers rendering determinism, explicit/external credentials, FTL leaf semantics, listener consistency, NodePort allocation, storage identity, selector isolation, image compatibility, structured policy data, and rejection of DNS-error responses even when the `dig` process exits successfully. The CI workflow runs the suite plus explicit chart lint/install on the release SHA.

These checks do not close the remaining multi-node, policy-ownership/deletion, revision-retention, restrictive-CNI, storage-recovery or architecture/version-matrix gates. Read [MIGRATION.md](../../charts/pihole/MIGRATION.md) before any v1 adoption.

The final v2 regression completed on 2026-10-29 with eight passing integration checks and 16 passing chart contract tests. Required pre-commit hooks passed for both tracked and added files, including chart-testing lint, ShellCheck, generated docs/schema, and KubeLinter. The packaged alpha chart passed lint and rendered its embedded API importer. The JSON report records the package digest; this evidence remains limited to the stated single-node amd64 environment.

## Retained homelab staging

On 2026-11-14, the alpha was deployed as `pihole-staging` on `admin@homelab`, Kubernetes 1.37.0, Talos 1.14.1 and Cilium 1.20.2. Two replicas use separate retained local PVs on `cp01` and `cp03`. Talos directory-backed user volumes were added in no-reboot mode without formatting or repartitioning. They share the existing `/var` filesystem and have no enforced 1Gi quota. Staging remains running; existing DNS and client/router settings are unchanged.

[Eight passing live checks](homelab-staging.json) establish correct PVC/Service/PDB selection; UDP/TCP local, blocked and upstream queries through the Service, individual pods and workstation NodePorts; authenticated exporter status; persistence across replacement of each DNS pod; versioned Secret rotation and subsequent authentication/DNS; and live Prometheus scraping with three healthy inactive alert rules. Sixteen sampled queries during the two pod replacements had zero observed failures. This is a small observation, not an availability SLA.

The [first run](homelab-staging-first-run.json) failed on an empty marker without checking its contents before replacement. A corrected test verifies and syncs each write first; both volumes subsequently passed. No transport/root-cause diagnosis is claimed from that first assertion. The [next run](homelab-staging-before-monitoring-fix.json) passed storage, DNS and rotation but found a Prometheus scrape timeout. Hubble confirmed Prometheus's existing Cilium egress allowlist denied TCP 9617. A separate staging-only endpoint/port rule resolved it, after which monitoring alone was rechecked and the final report completed.

The supplied [staging runbook](../../deploy/staging/README.md) records exact manifests, NodePort access, narrow workstation ingress, private administration and retained credential versions. Validation client pods and diagnostic markers were removed. No node shutdown/drain, disk restoration, external load-balancer failure, notification delivery or continuous synthetic DNS monitor was tested/installed. These and full policy ownership/revisions remain production gates.

## Alpha.2 policy lifecycle and staging upgrade

The new [policy lifecycle fixture](policy-runtime.json) passed eleven cases against the pinned image in a dedicated `pihole-policy-validation` namespace, then removed that namespace. It exercises empty-policy startup; owned create/update/delete including the last rule; unmanaged collisions and explicit adoption; group/client associations and unmanaged dependency protection; allow/block subscriptions and persisted gravity-dirty cleanup; ownership-marker conflicts; invalid references/checksums; simulated post-create/pre-journal-commit recovery; rollback after a partial semantic API failure; retained immutable Helm revision rollback; and positive/negative Helm and scheduled DNS checks under real network policy. This fixture uses deliberately seeded empty lists and TEST-NET bootstrap aliases; production staging retains the real bootstrap path.

Failures were retained during development. The first run exposed client display-name versus identifier handling. The functional test exposed the inherited LOCAL listening mode blocking cross-pod queries in this Cilium setup; explicit ALL fixed it, while NetworkPolicy still restricts clients. A pending-intent fixture edit originally produced an invalid journal; it was replaced by an encoded, synced and verified fixture write. Corrected results distinguish fixture corrections from chart fixes. The final suite verifies negative answers really fail, rather than accepting only successful HTTP/process exits.

The real alpha.1-to-alpha.2 upgrade then found dynamic chart/app-version labels in immutable claim templates. A regression now changes both versions and verifies claim templates stay identical. [Migration evidence](homelab-alpha2-migration.json) records the guarded controller orphan/recreation, unchanged storage specs, retained running pods and original PVC UIDs, consistent pre-adoption gravity backups, and explicit policy adoption. An ordinary second upgrade succeeded without force flags or controller recreation, with adoption disabled.

[Five final staging checks](homelab-alpha2-runtime.json) passed: both persistent ownership journals and readiness markers match the immutable revision; the stronger Helm test succeeds; workstation UDP/TCP NodePort filtering works on both DNS nodes; a real scheduled DNS check succeeds after rollout; and Prometheus scrapes both status samples with four healthy inactive alert rules. The CronJob runs once per minute against the Service and every replica, with bounded job history and no credential mount. Existing DNS/router settings remain unchanged and staging stays running.

There are now 23 passing chart contract regressions, including immutable artifact references and GitOps retention/tamper rejection, false-positive metrics rejection, monitoring placement, restricted test/probe workloads and claim-template version stability. Required formatting, chart-testing, docs/schema and KubeLinter hooks passed. The Docker-based ShellCheck hook could not start because Colima was stopped; the equivalent official native ShellCheck 0.11.0 checked both scripts successfully (download archive SHA-256 `56affdd8de5527894dca6dc3d7e0a99a873b0f004d7aabc30ae407d3f48b0a79`).

Remaining production gates include controlled list snapshots and refresh coordination, concurrent mutation boundaries, off-node backup restoration, abrupt node/VIP failures, external/site probes, alert notification delivery, capacity testing, arm64/version coverage and release security/publication gates. The minute-based in-cluster checks are not an external availability SLA.

## Latest dependency refresh: alpha.3

Chart `2.0.0-alpha.3` combines the v2 implementation with the upstream Pi-hole `2026.11.0`, exporter `v1.3.1`, Helm `4.3.2` and newer pinned CI actions. Three upstream/stashed merge conflicts were resolved without reverting to the v1 workload or changed-chart-only CI. Docker Hub index/platform digests and official tagged sources are recorded in [latest-upstream-review.json](latest-upstream-review.json). The reviewed FTL configuration registry has 166 explicitly initialized keys in both versions, with no added/removed keys; all four policy API specifications were unchanged. Runtime compatibility was still tested because startup/config implementation and Core privilege tooling changed.

[Thirteen isolated cases](latest-policy-runtime.json) passed under Helm 4.3.2. They include a same-pod retained-volume upgrade from the previous pinned image, preserved filtering/journal identity/database integrity, new-exporter authentication to an old server during transition, installed FTL 6.9/Core 6.6/Web 6.6.1 verification, and the full ownership/rollback/positive-and-negative functional suite. The isolated namespace was removed. This retained `emptyDir` case preserves the volume across a container image restart; the separate staging test exercises actual PVCs.

[Six staging checks](latest-dependency-upgrade.json) passed after consistent migration backups. An ordinary upgrade preserved the controller, both original PVCs and ownership identities with adoption disabled. The new images passed Helm tests, external UDP/TCP filtering, a fresh scheduled DNS success and Prometheus scraping with four healthy inactive rules. Staging remains running with the new versions; production DNS/router settings were unchanged.

The first attempt hit a transient API timeout and cleaned up. Additional retained development results record two fixture corrections: component checks now inspect installed Git versions and the FTL binary instead of asynchronous version-cache contents, and rollback captures the clean baseline revision rather than assuming revision 1 after adding a prior-image bootstrap. Neither fixture issue required weakening the application checks.

The 23 chart contract regressions, required chart hooks, native ShellCheck, workflow actionlint and packaged-chart lint passed. Workflow lint also found and corrected shell path quoting and PR-body construction in the updated release workflows. GitHub Actions/publication itself was not triggered. The exact Helm archive checksum, action references, package checksum and evidence links are recorded in the latest validation summary. Existing production-certification limits remain open.

## Stable 2.0.0 validation

The portable DNS chart is now versioned `2.0.0` against Pi-hole `2026.11.0` (FTL 6.9/Core 6.6/Web 6.6.1) and exporter `v1.3.1`. The authoritative [stable summary](stable-validation.json) binds the local reports to the exact canonical archive SHA-256. Its source checkout is uncommitted/dirty, so this local package is unsigned and deliberately cannot be published by the release tooling. A clean successful main-push CI run and manual promotion remain required. No remote CI or external release was invoked.

[ARM64/Helm 4](stable-arm64-policy-final.json) and [amd64/Helm 3](stable-amd64-policy-final.json) each passed 15 lifecycle cases for the final archive. They include the prior-image retained-data upgrade, component versions, owned CRUD/deletion/adoption, protected dependencies, interrupted intent repair, partial rollback, retained Helm rollback, pinned HTTPS snapshots, provider-down operation, preservation of serving readiness during unchanged scheduled checks, forced session revocation before a POST with exactly one successful mutation, and positive/negative functional/synthetic checks. Split TCP/UDP Services with distinct custom ports also pass. The isolated namespaces were removed.

Earlier hardened-source runs cover [Kubernetes 1.35.8/Helm 3](stable-kubernetes135-arm64.json) and [1.36.4/Helm 4](stable-kubernetes136-arm64.json) on ARM64. The final archive lifecycle ran on Kubernetes 1.36.4/ARM64 and 1.37.0/amd64; its separate HA fixture uses 1.37.0/ARM64. A final [Kubernetes 1.35.8/Helm 3 functional smoke](stable-final-kubernetes135-smoke.json) passed three checks against that same archive and removed its namespace. CI is configured to test all 12 combinations of the three minors, two Helm versions and two architectures, using one prebuilt archive. Configuring that matrix is not evidence that GitHub Actions ran.

The [generic kind HA/TLS/recovery fixture](stable-kind-ha-recovery-final.json) starts two independent retained PVC-backed replicas on separate worker nodes with 100,001 deterministic domains. It verifies a custom read-only TLS Secret, a trusted CA and correct hostname, rejection of wrong-host/untrusted certificates, and HTTPS Service port 443 routing to internal 8443 from a separate client pod. An external Docker peer verifies client-specific filtering and source-IP preservation through this MetalLB L2/Local profile. Bounded load has no failures; exact QPS/latency and the rate-limit override are in the report. SIGKILL of the announcing kind worker causes observed query failures followed by stable UDP/TCP recovery. These virtual-node observations do not establish hardware, provider-independent or site availability guarantees.

The same fixture streams an age-encrypted off-cluster backup, restores it into an empty claim in a fresh namespace with the list provider unavailable, and verifies identical ownership identities, filtering, authentication, metrics and TLS trust from independently recreated Secrets. It rejects a wrong archive checksum before helper creation and refuses active-claim overwrite. Query history is intentionally excluded; original Helm values, credentials and external Secret sources remain separate recovery dependencies. See [the recovery runbook](../recovery.md).

[Six retained staging checks](stable-staging-upgrade.json) passed after consistent migration backups. The exact stable archive preserved the StatefulSet controller, both original PVCs and ownership identities; both replicas remain Ready. UDP/TCP DNS, filtering, upstreams, authentication, scheduled DNS and live Prometheus scraping/rules passed with explicit DNS/exporter egress policies. [Node cp01](stable-egress-pod0.json) and [node cp03](stable-egress-pod1.json) each passed a negative egress proof: an unselected same-node control received HTTP 301, while the selected DNS pod timed out on that same HTTP destination. Control pods were removed. No router/client DNS settings, physical node power state or shared monitoring egress policy was changed.

[Security gating](stable-security-final.json) passed the final rendered manifests and both application images on amd64 and arm64 using an isolated fresh vulnerability database, with no HIGH/CRITICAL image vulnerabilities and two narrowly scoped documented manifest exceptions. Empty/skipped scans are rejected. Thirty chart/release regressions, required hooks (including the real Docker ShellCheck hook), workflow actionlint, real OpenPGP verification/tamper rejection and real signed OCI push/pull/resume checks passed. GitHub asset retry and Pages build/delivery tests use simulated APIs; those external publication paths still need execution during manual release.

Retained development failures distinguish fixture/environment issues from fixes: a stock-rate-limited throughput fixture, kubelet reconnection after a crash, BusyBox flock option compatibility, separate-client TLS Service checks, emulated slow API session rejection, an unavailable Trivy mirror, and local inotify exhaustion while hosting multiple kind clusters. The VM-only inotify limit was increased; no homelab host tuning was applied. Current fixes retain bounded deadlines, semantic DNS/TLS validation, ownership checks and image pins.

Production acceptance remains specific to the chosen deployment: durable storage and enforced capacity, long-term history/list sizing, actual CNI/load-balancer topology and source IPs, independent bootstrap/upstreams, continuous external/site probing and alert notification delivery. The default stock-image profile has root startup/writable root files and does not claim Restricted Pod Security compatibility. DHCP/NTP HA, IPv6/dual-stack certification, autoscaling and instantaneous global policy cutover are outside this stable DNS scope.
