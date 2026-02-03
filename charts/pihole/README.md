# Pi-hole Helm Chart (v6 Ready)

[![Artifact HUB](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/nunoferna/pihole)](https://artifacthub.io/packages/search?repo=pihole)

A production-ready Helm chart for Pi-hole, fully compatible with Pi-hole v6 FTL configuration variables.

## Features
- **Full FTL Control**: Configure `pihole-FTL.toml` settings directly via `values.yaml` (nested under `pihole.ftl`).
- **Split Services**: Dedicated LoadBalancer for DNS, ClusterIP for Web.
- **Observability**: Built-in Prometheus Exporter sidecar ([pihole6_exporter](https://github.com/bazmonk/pihole6_exporter)).
- **Persistence**: Preserves `/etc/pihole` and `/etc/dnsmasq.d`.

## Prometheus Metrics
This chart includes support for the [pihole6_exporter](https://github.com/bazmonk/pihole6_exporter) which provides Prometheus metrics for Pi-hole v6.

**Note**: You'll need a Docker image of pihole6_exporter. You can build one from the [GitHub repository](https://github.com/bazmonk/pihole6_exporter) or use a community-maintained image.

## Configuration
See `values.yaml` for the complete list of 100+ configurable FTL options.