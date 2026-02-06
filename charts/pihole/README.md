# Pi-hole Helm Chart (v6 Ready) Beta Version

[![Artifact HUB](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/pihole-helm)](https://artifacthub.io/packages/search?repo=pihole-helm)

A production-ready Helm chart for Pi-hole, fully compatible with Pi-hole v6 FTL configuration variables.

### Install from OCI Registry

```bash
# Install from GitHub Container Registry
helm install pihole oci://ghcr.io/nunoferna/charts/pihole --version 0.2.7
```

## Features

- **Full FTL Control**: Configure `pihole-FTL.toml` settings directly via `values.yaml` (nested under `pihole.ftl`).
- **Split Services**: Dedicated LoadBalancer for DNS, ClusterIP for Web.
- **Observability**: Built-in Prometheus Exporter sidecar ([ekofr/pihole-exporter](https://hub.docker.com/r/ekofr/pihole-exporter)).
- **Persistence**: Preserves `/etc/pihole` and `/etc/dnsmasq.d`.

## Prometheus Metrics

This chart includes support for the [ekofr/pihole-exporter](https://hub.docker.com/r/ekofr/pihole-exporter) which provides comprehensive Prometheus metrics for Pi-hole.

The exporter is available as a ready-to-use Docker image with 5M+ pulls. Configure it via environment variables in `values.yaml` under the `metrics` section.

## Configuration

See `values.yaml` for the complete list of 100+ configurable FTL options.
