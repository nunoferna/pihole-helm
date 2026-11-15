# Backup and restore

`scripts/recovery.py` backs up one replica's configuration, consistent gravity database, ownership journal and verified subscription snapshots. Query history and credentials supplied by Kubernetes/external Secret systems must be retained separately. Replicas have independent databases: choose a known healthy source, rather than merging their files.

Requirements: kubectl, age, an explicit cluster context, access to the source pod and a healthy applied policy revision. Pause configuration changes in Helm and the UI during backup. The tool holds the same lock as policy reconciliation/cron and uses SQLite's backup API, including committed WAL data. It does not coordinate arbitrary UI writes or unrelated volume snapshots. Temporary plaintext staging exists only on the source PVC and is removed afterward. The workstation receives an encrypted stream.

Generate an age identity once in your secure key-management environment. Keep its private identity separately from backup storage. Supply only its public recipient to the backup process:

```sh
python3 scripts/recovery.py backup \
  --context my-cluster --namespace dns --pod pihole-0 \
  --recipient age1YOUR_PUBLIC_RECIPIENT \
  --output /secure-backups/pihole-0.age
```

An existing output file is never overwritten. Record the returned ciphertext SHA-256 in trusted metadata retained separately from the backup object, or in a signed backup catalog. Store encrypted archives outside the cluster and failure domain; this utility writes the specified local file and does not upload it to a provider. Maintain independently recoverable Helm values, pinned images, reviewed policy artifacts and versioned Secret sources. Back up before application upgrades, and test restores periodically.

## Restore to a separate namespace

Provision an empty claim using your chosen storage system before installing the replacement release. For release `pihole`, the default claim name for ordinal 0 is `etc-pihole-pihole-0`. Set its StorageClass, access mode and size to match the replacement chart values. Use durable production storage with appropriate topology and tested expansion behavior; the kind fixture uses disposable local-path storage only.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: etc-pihole-pihole-0
  namespace: dns-recovery
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: your-durable-storage
  resources:
    requests:
      storage: 1Gi
```

```sh
python3 scripts/recovery.py restore \
  --context my-cluster --namespace dns-recovery \
  --pvc etc-pihole-pihole-0 --identity /secure-keys/pihole.agekey \
  --input /secure-backups/pihole-0.age \
  --expected-sha256 EXPECTED_SHA256_FROM_TRUSTED_CATALOG
```

The restore first verifies the expected ciphertext hash, creates a short-lived helper pod, refuses a claim referenced by any pod or containing existing data, verifies the image/backup metadata and SQLite integrity, and removes the helper. Encryption does not establish who created an archive; use the independently trusted checksum rather than computing a new expected value from an untrusted downloaded object. Backup rejects links/special files. Restore streams decrypted bytes directly into the helper without writing a plaintext workstation archive. A failed or interrupted extraction leaves `.restore-incomplete`; the default chart startup refuses that volume. Investigate the failure and use a fresh empty claim for a retry.

Restore with the same image that created the backup; `--image` selects a deliberately retained older image when required. Restore does not downgrade a database schema. The default is the current compatibility image, and an image mismatch fails rather than guessing a migration path.

Recreate credentials independently. Install the same desired policy with `policy.owner` set to the original namespace/release identity reported by the tool, for example:

```yaml
policy:
  owner: dns/pihole
  requirePinnedLists: true
pihole:
  web:
    existingSecret: pihole-web-v1
```

Keep the original desired lists/rules, UID/GID, listener and persistence settings in the values used for installation. The restored ownership identity remains explicit; do not change journal owners or enable broad adoption to bypass an identity failure. Use a separate DNS address for validation, run `helm test`, verify every replica and the client-facing address over UDP/TCP, then perform your environment's controlled DNS cutover. Keep the original installation and backup during the rollback window.

Controlled snapshots retain the exact verified bodies on each PVC, allowing provider-down reconciliation and gravity rebuilds after restore. Fresh cold bootstrap still needs an independent DNS resolver for the stock image's gravity prerequisite. Restore of one lost claim and total-cluster recovery have different infrastructure and credential dependencies; this tool does not claim site recovery or an availability SLA.
