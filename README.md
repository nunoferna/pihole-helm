# Pi-hole Helm Chart

[![Artifact HUB](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/pihole-helm)](https://artifacthub.io/packages/search?repo=pihole-helm)

A production-ready Helm chart for deploying [Pi-hole v6](https://pi-hole.net/) on Kubernetes, with full FTL configuration support, high-availability, and built-in observability.

## Features

- **Pi-hole v6**: Full FTL configuration via `pihole-FTL.toml` — DNS, DHCP, NTP, webserver, database, and more
- **High Availability**: Scales to multiple replicas using a StatefulSet with per-pod PVCs; web UI pins to pod-0, DNS is load-balanced across all pods
- **Declarative List Management**: Adlists, allowlists, blocklists, regex rules, groups, and clients managed via `values.yaml` and applied idempotently on every `helm upgrade`
- **Automatic Config Sync**: A checksum annotation on the pod template forces a pod restart whenever list configuration changes, ensuring `postStart` re-runs and the Pi-hole database stays in sync
- **Observability**: Standalone Prometheus metrics exporter ([ekofr/pihole-exporter](https://hub.docker.com/r/ekofr/pihole-exporter)) with auto-generated multi-instance scrape targets, optional ServiceMonitor, and bundled Grafana dashboard
- **Secret Management**: Supports plain values, existing Kubernetes Secrets, HashiCorp Vault Agent Injector, and External Secrets Operator
- **Network Policies**: Optional NetworkPolicy to restrict ingress to DNS, DHCP, and web ports
- **Pod Disruption Budget**: Optional PDB to guarantee minimum availability during cluster maintenance
- **GPG Signed**: Charts are signed for supply chain security
- **OCI & Helm Repo**: Available via GitHub Releases (Helm repository) and GitHub Container Registry (OCI)

## Requirements

| Dependency | Version     |
| ---------- | ----------- |
| Kubernetes | `>= 1.23.0` |
| Helm       | `>= 3.0.0`  |

## Quick Start

```bash
helm repo add pihole-helm https://nunoferna.github.io/pihole-helm/
helm repo update
helm install pihole pihole-helm/pihole --set pihole.web.password=yourpassword
```

Or from OCI:

```bash
helm install pihole oci://ghcr.io/nunoferna/charts/pihole --set pihole.web.password=yourpassword
```

## Configuration

### Workload Topology

| `replicas` | Workload kind | Update strategy | PVC                               |
| ---------- | ------------- | --------------- | --------------------------------- |
| `1`        | `Deployment`  | `Recreate`      | Single shared PVC                 |
| `> 1`      | `StatefulSet` | `RollingUpdate` | Per-pod PVC (VolumeClaimTemplate) |

When running as a StatefulSet, the web Service selects only `pod-0` (stable UI endpoint) while the DNS Service load-balances across all pods. All pods share identical blocklist configuration thanks to the Helm-driven import script.

### Web Password

```yaml
# Plain value (auto-generates a random password if left empty)
pihole:
  web:
    password: "your-secure-password"

# Existing Kubernetes Secret
pihole:
  web:
    existingSecret: "my-secret"
    passwordSecretKey: "password"

# External injection (Vault, ESO) — skip the env var entirely
pihole:
  web:
    skipPasswordEnv: true
```

See [examples/values-vault.yaml](examples/values-vault.yaml) and [examples/values-external-secrets.yaml](examples/values-external-secrets.yaml) for full secret management patterns.

### DNS

```yaml
pihole:
  ftl:
    dns:
      upstreams:
        - 1.1.1.1
        - 8.8.8.8
      dnssec: true
      queryLogging: true
      cache:
        size: 10000
      service:
        type: LoadBalancer
        externalTrafficPolicy: Local
        # Split TCP and UDP into separate services (different IPs, types per protocol)
        splitTcpUdp: false
```

### DHCP

```yaml
pihole:
  ftl:
    dhcp:
      active: true
      start: "192.168.1.100"
      end: "192.168.1.200"
      router: "192.168.1.1"
      service:
        enabled: true
        type: LoadBalancer
```

### List Management

All list entries are applied via `INSERT OR IGNORE` into Pi-hole's `gravity.db` on every pod start and on every `helm upgrade` that changes list configuration. Deletions from `values.yaml` are **not** purged from the database (idempotent inserts only).

```yaml
pihole:
  lists:
    adlists:
      - address: "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
        comment: "StevenBlack Master"
      - address: "https://adaway.org/hosts.txt"
        enabled: false

    whitelist:
      - domain: "good-site.com"

    blacklist:
      - domain: "bad-site.com"
        comment: "Known malware"

    regex_whitelist:
      - domain: ".*\\.trusted\\.com$"

    regex_blacklist:
      - domain: "(^|\\.)ads\\..*"

  groups:
    - name: "default"
      enabled: true
      description: "Default group"

  clients:
    - id: "192.168.1.100"
      comment: "My PC"
      groups:
        - "default"
    - id: "aa:bb:cc:dd:ee:ff"
      comment: "My Phone"
      groups:
        - "default"
```

### Persistence

```yaml
persistence:
  etcPihole:
    enabled: true
    size: 500Mi
    accessMode: ReadWriteOnce
    # storageClassName: ""
```

### Observability

The metrics exporter runs as a standalone `Deployment` (always 1 replica) and scrapes all Pi-hole instances. Connection targets (`PIHOLE_HOSTNAME`, `PIHOLE_PORT`, `PIHOLE_PROTOCOL`) are auto-generated from the number of replicas — no manual configuration needed.

- **Deployment** (1 replica): targets the web ClusterIP Service
- **StatefulSet** (N replicas): targets each pod directly via headless DNS (`pod-0.pihole-headless`, `pod-1.pihole-headless`, ...)

```yaml
metrics:
  enabled: true
  service:
    enabled: true
    type: ClusterIP
  serviceMonitor:
    enabled: true # Requires Prometheus Operator
    interval: 30s
  dashboards:
    enabled: true # Requires Grafana with sidecar
    label: "grafana_dashboard"
    labelValue: "1"
  env:
    - name: INTERVAL
      value: "10s"
    # Optional: authenticate with Pi-hole API password
    # - name: PIHOLE_PASSWORD
    #   valueFrom:
    #     secretKeyRef:
    #       name: pihole-secret
    #       key: password
    # Override auto-generated connection settings (e.g. for HTTPS):
    # - name: PIHOLE_PROTOCOL
    #   value: "https"
```

The bundled Grafana dashboard ([ID: 10176](https://grafana.com/grafana/dashboards/10176)) provides query rates, blocking rates, top domains/clients, and cache statistics.

See [examples/values-monitoring.yaml](examples/values-monitoring.yaml) for the full monitoring stack example.

### Network Policy

```yaml
networkPolicy:
  enabled: true
  # Restrict web UI and metrics access to specific namespaces.
  # DNS (port 53) and DHCP (port 67) are always open.
  # Leave empty to allow all namespaces.
  webAllowFrom:
    - namespaceSelector:
        matchLabels:
          name: monitoring
```

### Pod Disruption Budget

```yaml
podDisruptionBudget:
  enabled: true
  minAvailable: 1
```

### Extra Customisation

```yaml
# Extra environment variables
extraEnv:
  - name: MY_VAR
    value: "value"

# Extra init containers (e.g. for secret transformation)
extraInitContainers: []

# Pod scheduling
nodeSelector: {}
affinity: {}
tolerations: []

# Cluster DNS domain (override only if non-default)
clusterDomain: "cluster.local"
```

## High Availability

```yaml
replicas: 3

persistence:
  etcPihole:
    enabled: true
    accessMode: ReadWriteOnce # Each pod gets its own PVC

podDisruptionBudget:
  enabled: true
  minAvailable: 2
```

With 3 replicas:

- DNS queries are load-balanced across all 3 pods (active-active)
- Web UI is served exclusively by pod-0 (stable endpoint)
- Metrics exporter scrapes all 3 pods via headless DNS
- A rolling restart upgrades one pod at a time with no DNS outage

## Secret Management Examples

| Method                    | File                                                                           |
| ------------------------- | ------------------------------------------------------------------------------ |
| Kubernetes Secret         | `pihole.web.existingSecret`                                                    |
| HashiCorp Vault Agent     | [examples/values-vault.yaml](examples/values-vault.yaml)                       |
| External Secrets Operator | [examples/values-external-secrets.yaml](examples/values-external-secrets.yaml) |

## Development

```bash
# Lint
helm lint charts/pihole

# Template (dry-run)
helm template pihole charts/pihole

# With metrics and 3 replicas
helm template pihole charts/pihole --set metrics.enabled=true --set replicas=3

# Install with dry-run
helm install pihole charts/pihole --dry-run --debug
```

## Links

- [Chart README](charts/pihole/README.md) — full values reference
- [Pi-hole Documentation](https://docs.pi-hole.net/)
- [Pi-hole Docker](https://github.com/pi-hole/docker-pi-hole)
- [ekofr/pihole-exporter](https://github.com/eko/pihole-exporter)
- [Artifact Hub](https://artifacthub.io/packages/search?repo=pihole-helm)

## License

Apache-2.0 — see [Chart.yaml](charts/pihole/Chart.yaml).
