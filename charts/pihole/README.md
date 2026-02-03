# Pi-hole Helm Chart (v6 Ready)

[![Artifact HUB](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/nunoferna/pihole)](https://artifacthub.io/packages/search?repo=pihole)

A production-ready Helm chart for Pi-hole, fully compatible with Pi-hole v6 FTL configuration variables.

## Features
- **Full FTL Control**: Configure `pihole-FTL.toml` settings directly via `values.yaml` (nested under `pihole.ftl`).
- **Split Services**: Dedicated LoadBalancer for DNS, ClusterIP for Web.
- **Observability**: Built-in Prometheus Exporter sidecar.
- **Persistence**: Preserves `/etc/pihole` and `/etc/dnsmasq.d`.

## Configuration
See `values.yaml` for the complete list of 100+ configurable FTL options.