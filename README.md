# Pi-hole Helm Chart Repository

[![Artifact HUB](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/pihole-helm)](https://artifacthub.io/packages/search?repo=pihole-helm)

A production-ready Helm chart repository for deploying [Pi-hole](https://pi-hole.net/) on Kubernetes, with full support for Pi-hole v6 and FTL configuration.

## Overview

This repository contains an enterprise-grade Helm chart for Pi-hole, a network-wide DNS-based ad blocker and local DNS server. The chart is designed for Kubernetes deployments and provides comprehensive configuration options that map directly to Pi-hole's FTL configuration file (`pihole-FTL.toml`).

## Features

- **🎯 Pi-hole v6 Ready**: Full compatibility with Pi-hole v6 and complete FTL configuration support
- **⚙️ 100+ Configuration Options**: Fine-grained control over DNS, DHCP, webserver, database, and more
- **📊 Built-in Observability**: Integrated Prometheus metrics exporter sidecar ([ekofr/pihole-exporter](https://hub.docker.com/r/ekofr/pihole-exporter))
- **🔌 Split Services**: Dedicated LoadBalancer for DNS, ClusterIP for Web UI, optional DHCP service
- **💾 Persistent Storage**: Data persistence for `/etc/pihole` and `/etc/dnsmasq.d`
- **🔒 Security Features**: NetworkPolicy support, Pod Disruption Budget, and configurable security contexts
- **🔐 GPG Signing**: Charts are signed with GPG for supply chain security
- **📦 Multiple Distribution Methods**: Available via GitHub Releases and GitHub Container Registry (OCI)

## Quick Start

### Add the Helm Repository

```bash
helm repo add pihole-helm https://nunoferna.github.io/pihole-helm/
helm repo update
```

### Install Pi-hole

```bash
# Basic installation with default values
helm install pihole pihole-helm/pihole

# Installation with custom values
helm install pihole pihole-helm/pihole \
  --set pihole.web.password=yourpassword \
  --set serviceDns.type=LoadBalancer \
  --set persistence.etcPihole.enabled=true
```

### Install from OCI Registry

```bash
# Install from GitHub Container Registry
helm install pihole oci://ghcr.io/nunoferna/charts/pihole --version 0.1.4
```

## Configuration

The chart provides extensive configuration options. Here are some key areas:

### DNS Configuration

```yaml
pihole:
  ftl:
    dns:
      upstreams:
        - "8.8.8.8"
        - "1.1.1.1"
      dnssec: false
      queryLogging: true
      cache:
        size: 10000
```

### Web Password

```yaml
pihole:
  web:
    password: "your-secure-password"
    # Or use existing secret
    # existingSecret: "pihole-secret"
    # passwordSecretKey: "password"
```

### Services

```yaml
serviceDns:
  enabled: true
  type: LoadBalancer
  externalTrafficPolicy: Local

serviceWeb:
  enabled: true
  type: ClusterIP
```

### Metrics & Monitoring

```yaml
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
    interval: 30s
```

### Persistence

```yaml
persistence:
  etcPihole:
    enabled: true
    size: 500Mi
  etcDnsmasq:
    enabled: true
    size: 100Mi
```

For the complete list of configuration options, see the [chart's values.yaml](charts/pihole/values.yaml) or the [chart README](charts/pihole/README.md).

## Documentation

- **[Chart README](charts/pihole/README.md)**: Detailed chart documentation
- **[values.yaml](charts/pihole/values.yaml)**: Complete configuration reference with inline comments
- **[values.schema.json](charts/pihole/values.schema.json)**: JSON schema for values validation

## Prometheus Metrics

This chart includes optional support for the [ekofr/pihole-exporter](https://hub.docker.com/r/ekofr/pihole-exporter), a widely-used Prometheus exporter with 5M+ Docker Hub pulls. When enabled, it runs as a sidecar container and exposes metrics on port 9617.

Enable the exporter with:
```yaml
metrics:
  enabled: true
  env:
    - name: PIHOLE_HOSTNAME
      value: "127.0.0.1"
    - name: PIHOLE_PORT
      value: "80"
    - name: INTERVAL
      value: "10s"
    # Optional: Use password or API token authentication
    # - name: PIHOLE_PASSWORD
    #   valueFrom:
    #     secretKeyRef:
    #       name: pihole-secret
    #       key: password
```

The exporter provides comprehensive metrics including DNS queries, blocked ads, top domains, forward destinations, and more.

## Chart Versioning

This chart follows [Semantic Versioning](https://semver.org/):
- **Chart Version**: Version of the Helm chart itself (e.g., `0.1.4`)
- **App Version**: Version of Pi-hole container (e.g., `2025.11.1`)

## Requirements

- Kubernetes: `>=1.23.0`
- Helm: `>=3.0.0`

## Security

### Chart Signing

All chart releases are automatically signed using GPG. This ensures chart authenticity and integrity.

The GPG public key is available at: https://nunoferna.github.io/pihole-helm/pubkey.gpg

To verify a chart signature:
```bash
# Import the public key
curl -L https://nunoferna.github.io/pihole-helm/pubkey.gpg | gpg --import

# Download and verify a chart
helm pull pihole-helm/pihole --version 0.1.4
helm verify pihole-0.1.4.tgz
```

### NetworkPolicy

Enable NetworkPolicy to restrict pod network access:
```yaml
networkPolicy:
  enabled: true
  webAllowFrom:
    - namespaceSelector:
        matchLabels:
          name: monitoring
```

## Development

### Repository Structure

```
.
├── .github/
│   └── workflows/
│       └── release.yaml         # Automated release workflow
└── charts/
    └── pihole/
        ├── Chart.yaml           # Chart metadata
        ├── values.yaml          # Default configuration
        ├── values.schema.json   # Values validation schema
        ├── README.md            # Chart documentation
        └── templates/           # Kubernetes manifests
```

### Release Process

This repository uses GitHub Actions for automated releases:
1. Chart changes are pushed to the `main` branch
2. GitHub Actions workflow automatically:
   - Packages the chart
   - Signs it with GPG
   - Creates a GitHub Release with provenance files
   - Pushes to GitHub Pages (Helm repository)
   - Pushes to GitHub Container Registry (OCI)

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

### Testing Changes

```bash
# Lint the chart
helm lint charts/pihole

# Test installation with dry-run
helm install pihole charts/pihole --dry-run --debug

# Template the chart
helm template pihole charts/pihole
```

## License

This chart is distributed under the MIT License as specified in [Chart.yaml](charts/pihole/Chart.yaml).

## Maintainers

- **Nuno Fernandes** - [nuno.o.fernandes@outlook.pt](mailto:nuno.o.fernandes@outlook.pt)

## Links

- **Chart Repository**: https://nunoferna.github.io/pihole-helm/
- **Source Code**: https://github.com/nunoferna/pihole-helm
- **Pi-hole Official**: https://pi-hole.net/
- **Pi-hole Docker**: https://github.com/pi-hole/docker-pi-hole
- **Artifact Hub**: https://artifacthub.io/packages/search?repo=pihole

## Support

For issues and questions:
- Open an issue in this repository
- Check the [Pi-hole documentation](https://docs.pi-hole.net/)
- Visit the [Pi-hole community](https://discourse.pi-hole.net/)
