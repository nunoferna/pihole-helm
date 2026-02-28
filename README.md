# Pi-hole Helm Chart Repository

[![Artifact HUB](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/pihole-helm)](https://artifacthub.io/packages/search?repo=pihole-helm)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Welcome to the official Helm chart repository for deploying **Pi-hole v6** on Kubernetes.

## 🚀 How to use this repository

To add this repository to your local Helm client, run:

```bash
helm repo add pihole-helm https://nunoferna.github.io/pihole-helm/
helm repo update
```

To install the chart:

```bash
helm install pihole pihole-helm/pihole
```

Alternatively, you can install directly via OCI (no need to add the repo first):

```bash
helm install pihole oci://ghcr.io/nunoferna/pihole-helm/pihole
```

## 📚 Documentation & Source Code

This page simply hosts the Helm repository metadata. For full configuration options, values.yaml documentation, and source code, please visit:

- [Artifact Hub Listing](https://artifacthub.io/packages/helm/pihole-helm/pihole) (Recommended)

- [GitHub Repository](https://github.com/nunoferna/pihole-helm)
