# Policy lifecycle in v2

The default `policy.mode: reconcile` manages only explicitly configured groups, clients, subscriptions and domain rules. A journal on each independent PVC (`/etc/pihole/.helm-policy-state.json`) records resource identities and random ownership claims. Comments carry a claim marker; removing it or replacing an owned identity makes the runner refuse further changes. Do not edit managed records through the UI during reconciliation. The API has no cross-endpoint transaction or compare-and-swap guarantee, so concurrent administration remains unsupported.

Input structure, duplicate identities, group references, ownership collisions and unmanaged dependencies of deleted groups are checked before mutations. The built-in group ID 0 is protected. Each entry may specify `groups: [Default, another-group]`; omitted associations use Default, while an explicit empty list clears associations. Groups must exist already or be declared in the same policy. Adlists accept `type: block` (default) or `type: allow`, `enabled`, `comment` and `groups`. Exact domains use lowercase ASCII/Punycode; regex patterns remain literal JSON strings.

The runner creates groups before associations and deletes dependent owned records before groups. It refuses to delete a group still referenced by unmanaged records. Records absent from desired values are deleted only when the journal and API identity/claim agree. Unmanaged factory lists and manual rules remain. **An empty desired policy still runs**, allowing the last owned rule to be removed.

`policy.mode: additive` retains removed owned records. `policy.mode: disabled` freezes management, including deletion; it does not erase the journal or undo prior policy. Returning to reconciliation resumes ownership checks. Reconciliation runs at pod startup and through the stock weekly gravity schedule, not as a continuous controller. It repairs configured fields while the identity/claim remains intact; it cannot promise immediate repair of subsequent UI edits. Treat owned configuration as declarative and rebuild/restart deliberately after changes.

`policy.adoptExisting: true` explicitly permits adoption of records whose type/key match desired input. Review/export those records first and disable adoption after migration. Adoption does not authorize taking over an existing mismatched journal identity. A missing/corrupt journal needs operator recovery from a consistent backup; do not delete it simply to bypass a conflict.

The local runner is locked and bounded by `policyImportTimeout`. It writes mutation intent before API calls and atomically replaces/fsyncs the journal after updates. A unique claim recovers the window between creation and identity commit. List changes persist a dirty flag until gravity succeeds, including removal of the last subscription. Failure withholds readiness; restarting or applying a retained revision repairs the journaled partial operation. API mutations are not a globally atomic policy switch: old and new revisions can coexist during a rollout. The standard profile still permits mutable upstream feeds, so its replicas can download different content. Prefer the controlled snapshot profile below for reproducible production filtering.

## Artifacts and rollback

Every chart-managed artifact is immutable, content-named and annotated `helm.sh/resource-policy: keep`. Its name includes policy and runner content. Startup verifies the policy JSON checksum and release identity. Startup/readiness require the applied checksum; liveness tests local DNS independently. Credentials and native FTL settings are outside the policy JSON; their rollout behavior is unchanged.

Retained artifacts are deliberately not automatically garbage-collected. Keep every artifact referenced by running pods, StatefulSet ControllerRevisions and the supported Helm/GitOps rollback window. Manually remove only unreferenced artifacts after that window. `helm uninstall` may leave them behind; deleting the namespace removes them. Retention is not a database backup.

For explicit GitOps retention, generate an artifact set and a separate active-reference values overlay:

```sh
python3 scripts/policy-artifact.py --release pihole --namespace dns \
  -f values.yaml --retain retained-policies.yaml \
  --output policies.yaml --reference-output active-policy.yaml
```

Omit `--retain` for the first generation. The tool needs Helm, jq and PyYAML, writes no Secret and makes no cluster calls. It rejects mismatched ownership, content checksums, conflicting artifacts and oversized artifacts. Keep large list bodies outside ConfigMaps. Commit/review both files together. Apply the active-and-retained artifact set before the Helm release; add `-f active-policy.yaml` to select `policy.existingConfigMap` and `policy.revision`. Configure GitOps dependencies/pruning so retained artifacts remain until safe to collect. Keep retained copies of the runner files too; an old policy reference must use its matching runner.

Rollback within this lifecycle model reselects retained content and reconciles owned data back to that desired revision. It does not undo application schema migrations, restore lost query history or recover a disk. See the [encrypted recovery procedure](recovery.md) for a fresh namespace or lost claim; it retains ownership identity through an explicit `policy.owner` override.

## Moving from alpha.1

Alpha.1 did not record ownership. Back up/export first, retain its legacy ConfigMap, review the desired records and enable explicit adoption for one migration rollout. Once every replica reports the expected revision and owned identities, disable adoption and roll again. Reverting to the old additive runner can remove claim markers; a plain downgrade across that boundary is not a supported database rollback. Use the saved installation/data and a rehearsed recovery procedure if necessary.

Also follow the [claim-template migration procedure](../charts/pihole/MIGRATION.md): alpha.1 included changing version labels in immutable claim templates. Alpha.2 makes these labels stable. Existing alpha.1 controllers need guarded orphan/recreation while retaining running pods and exact PVC identities; subsequent upgrades use the stable templates normally.

## Tests and recurring checks

`helm test` uses the same pinned image with an overridden command, a read-only root filesystem, no API token, dropped capabilities and a non-root UID. It asserts local DNS answers over UDP/TCP through the Service and directly to every replica. Optional `validation.blockedDomain`, `.blockedAnswer` (including NXDOMAIN) and `.upstreamDomain` add filtering/upstream checks without installing sample rules. Authentication uses a read-only Secret mount and checks every replica plus the web Service. Metrics checks require an enabled blocking-status sample for every expected hostname, not just HTTP 200. With injected credentials, supply `tests.existingSecret` or explicitly disable authentication tests.

`monitoring.syntheticDns.enabled` schedules these DNS checks every minute by default, without credential access. It covers the in-cluster Service and all replicas; it does not traverse an external VIP. Jobs do not overlap, have a deadline, and retain bounded success/failure history. Enable `monitoring.alerts.enabled` for stale-success and replica/exporter/blocking rules; ServiceMonitor/PrometheusRule placement uses `monitoring.namespace` and common discovery labels. The stale-success threshold defaults to 180 seconds plus the alert `for` period (2 minutes). Choose schedule, check deadline and thresholds together. Exporter samples can remain stale while APIs fail; scheduled DNS checks provide separate evidence.

With NetworkPolicy enabled, `networkPolicy.allowTests` permits only same-release test/probe-labelled pods on required ports. Disable it to provide a custom policy. Scraping also needs the monitoring namespace's egress permissions; the homelab-specific Cilium example is separate from the portable chart. Independent external probing and notification-delivery tests remain production requirements.

## Controlled subscription snapshots

Set `policy.requirePinnedLists: true` and configure each enabled adlist with an HTTPS `address` and lowercase `sha256`. Promote new content by changing its hash in reviewed values; keep the origin immutable or use a controlled mirror. The runner downloads at most 64 MiB per body, permits HTTPS-only redirects, verifies the digest before API mutations and atomically caches it at `/etc/pihole/helm-snapshots/<sha256>.txt`. FTL subscriptions point at these verified local files. Large bodies are never stored in ConfigMaps.

The pinned profile rejects enabled unmanaged/unverified feeds, including a previously enabled factory feed. Disable unwanted unmanaged feeds explicitly before enabling this profile on existing data; do not use broad adoption to remove unrelated policy. A fresh pinned installation seeds an empty initial adlist file. The stock image still needs independent bootstrap DNS during gravity initialization; it must not depend on this Pi-hole release resolving its own prerequisites.

A cached body is hash-checked on every apply. An unavailable provider does not trigger a download when the verified body exists. Missing or corrupt content withholds readiness if it cannot be fetched and verified; the runner does not substitute unreviewed bytes. The weekly task uses the same bounded API runner and lock. Unchanged immutable snapshots need no gravity rebuild and keep the existing readiness marker during a successful scheduled check. Real list changes and pending interrupted rebuilds complete before the new revision becomes ready.

Snapshot cache files are intentionally retained for the supported rollback window, just like policy artifacts. Size PVCs for retained snapshot generations, gravity temporary files/backups and optional history. Remove only unreferenced bodies after the rollback window, under operational controls; never delete the active generation as an ad hoc space fix. Disk, mirror, bootstrap and site recovery requirements depend on your deployment.
