# Pi-hole Helm Chart Repository

[![Artifact HUB](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/nunoferna/pihole)](https://artifacthub.io/packages/search?repo=pihole)

A production-ready Helm chart repository for deploying [Pi-hole](https://pi-hole.net/) on Kubernetes, with full support for Pi-hole v6 and FTL configuration.

## Overview

This repository contains an enterprise-grade Helm chart for Pi-hole, a network-wide DNS-based ad blocker and local DNS server. The chart is designed for Kubernetes deployments and provides comprehensive configuration options that map directly to Pi-hole's FTL configuration file (`pihole-FTL.toml`).

## Features

- **🎯 Pi-hole v6 Ready**: Full compatibility with Pi-hole v6 and complete FTL configuration support
- **⚙️ 100+ Configuration Options**: Fine-grained control over DNS, DHCP, webserver, database, and more
- **📊 Built-in Observability**: Integrated Prometheus metrics exporter sidecar ([pihole6_exporter](https://github.com/bazmonk/pihole6_exporter))
- **🔌 Split Services**: Dedicated LoadBalancer for DNS, ClusterIP for Web UI, optional DHCP service
- **💾 Persistent Storage**: Data persistence for `/etc/pihole` and `/etc/dnsmasq.d`
- **🔒 Security Features**: NetworkPolicy support, Pod Disruption Budget, and configurable security contexts
- **🔐 Keyless Signing**: Charts are signed with Sigstore/Cosign for supply chain security
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
helm install pihole oci://ghcr.io/nunoferna/charts/pihole --version 1.2.0
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

This chart includes optional support for the [pihole6_exporter](https://github.com/bazmonk/pihole6_exporter), which provides Prometheus metrics for Pi-hole v6. When enabled, it runs as a sidecar container and exposes metrics on port 9666.

**Note**: You'll need a Docker image of pihole6_exporter. You can build one from the [GitHub repository](https://github.com/bazmonk/pihole6_exporter) or use:
```yaml
metrics:
  enabled: true
  image:
    repository: bazmonk/pihole6_exporter
    tag: latest
```

## Chart Versioning

This chart follows [Semantic Versioning](https://semver.org/):
- **Chart Version**: Version of the Helm chart itself (e.g., `1.2.0`)
- **App Version**: Version of Pi-hole container (e.g., `2024.03.2`)

## Requirements

- Kubernetes: `>=1.23.0`
- Helm: `>=3.0.0`

## Security

### Chart Signing

All chart releases are automatically signed using Sigstore/Cosign with keyless signing. This ensures chart authenticity and integrity.

To verify a chart signature:
```bash
# Install the helm-sigstore plugin
helm plugin install https://github.com/sigstore/helm-sigstore

# Verify OCI chart
cosign verify ghcr.io/nunoferna/charts/pihole:1.2.0
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
   - Creates a GitHub Release
   - Pushes to GitHub Pages (Helm repository)
   - Pushes to GitHub Container Registry (OCI)
   - Signs charts with Cosign

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
