# Contributing

Contributions are welcome — bug reports, feature requests, and pull requests alike.

## Before You Start

- Check existing [issues](https://github.com/nunoferna/pihole-helm/issues) to avoid duplicating work.
- For significant changes, open an issue first to discuss the approach.

## Development Setup

**Prerequisites:**

- [Helm](https://helm.sh/docs/intro/install/) >= 3.0.0
- [chart-testing (`ct`)](https://github.com/helm/chart-testing)
- [kind](https://kind.sigs.k8s.io/) (for integration tests)
- [helm-docs](https://github.com/norwoodj/helm-docs) (for README generation)
- [pre-commit](https://pre-commit.com/) (runs all of the above automatically on commit)

Install and enable pre-commit hooks after cloning:

```bash
pip install pre-commit
pre-commit install
```

## Workflow

### 1. Fork and branch

```bash
git clone https://github.com/<your-fork>/pihole-helm.git
cd pihole-helm
git checkout -b feat/my-change
```

### 2. Make changes

All chart source lives under `charts/pihole/`. Key files:

| File | Purpose |
|---|---|
| `values.yaml` | Default values — always add `@schema`, `@section`, and `--` doc comments |
| `values.schema.json` | JSON schema — keep in sync with `values.yaml` additions |
| `templates/` | Kubernetes manifests |
| `templates/_helpers.tpl` | Named template helpers |
| `Chart.yaml` | Chart metadata and version |

### 3. Bump the chart version

Every change that affects chart behaviour or values **must** increment the chart version in `Chart.yaml` following [Semantic Versioning](https://semver.org/):

| Change type | Bump |
|---|---|
| Bug fix, documentation | `patch` |
| New feature, new value | `minor` |
| Breaking change | `major` |

You can also trigger the automated version bump workflow from GitHub Actions (`Actions → Bump Chart Version`) which opens a PR with the version incremented.

### 4. Run pre-commit

Pre-commit is the primary quality gate. It runs automatically on every `git commit` and covers:

| Hook | Purpose |
|---|---|
| `helmfmt` | Formats Helm templates consistently |
| `trailing-whitespace`, `end-of-file-fixer`, `mixed-line-ending` | File hygiene |
| `ct-lint` | `ct lint` — mirrors the CI lint job exactly |
| `shellcheck` | Lints shell scripts under `scripts/` |
| `helm-docs` | Regenerates `charts/pihole/README.md` from `values.yaml` annotations |
| `helm-schema` | Regenerates `values.schema.json` from `values.yaml` annotations |
| `kube-linter` | Checks templates against Kubernetes best practices |

To run all hooks manually without committing:

```bash
pre-commit run --all-files
```

To run a single hook:

```bash
pre-commit run helm-docs --all-files
pre-commit run helm-schema --all-files
pre-commit run ct-lint --all-files
```

> **Note:** `helm-docs` and `helm-schema` auto-modify files. Stage their output (`git add`) and commit again — this is expected behaviour.

### 6. Lint and test locally

```bash
# Lint the chart
helm lint charts/pihole

# Lint with chart-testing (same checks as CI)
ct lint --config .github/configs/ct.yaml

# Template rendering — check output looks correct
helm template test charts/pihole

# Template with specific scenarios
helm template test charts/pihole --set metrics.enabled=true --set replicas=3
helm template test charts/pihole --set pihole.ftl.dhcp.active=true --set pihole.ftl.dhcp.service.enabled=true

# Integration test against a local kind cluster
kind create cluster
ct install --config .github/configs/ct.yaml
```

### 7. Update documentation

If you added or changed values, regenerate the chart README:

```bash
helm-docs --chart-search-root charts/
```

`helm-docs` reads the `@schema`, `@section`, and `--` annotations from `values.yaml` to build the values table in `charts/pihole/README.md`.

> **Note:** If you have pre-commit installed, `helm-docs` runs automatically on commit. Only run this manually if you need to inspect the output before committing.

### 8. Open a pull request

- Target the `main` branch.
- Describe what the change does and why.
- Include a `helm template` snippet in the PR description if the output changed.

## CI Pipeline

PRs automatically trigger:

| Workflow | What it does |
|---|---|
| `Lint and Test Charts` | `ct lint` + `ct install` in a `kind` cluster |
| `Trivy IaC Scanner` | Scans templates for security misconfigurations |

The `Release Charts` workflow runs only on `main` after lint/test passes. It packages, GPG-signs, and publishes to GitHub Pages and GHCR.

## Adding Values

Follow the existing pattern so `helm-docs` and the JSON schema stay in sync:

```yaml
# @schema type:string,null
# -- (string) Description of this value shown in the README.
# @section -- Section Name
# @default -- "default-value"
myNewValue: "default-value"
```

And add the corresponding entry to `values.schema.json`:

```json
"myNewValue": {
    "description": "Description of this value shown in the README.",
    "type": ["string", "null"]
}
```

## Adding Templates

- Follow the Kubernetes naming convention: one resource per file, named after the resource kind (e.g., `deployment-metrics.yaml`, `service-dns.yaml`).
- Gate optional resources with a `{{- if .Values.someFeature.enabled }}` guard.
- Use `{{ include "pihole.fullname" . }}` for resource names and `{{ include "pihole.labels" . }}` for labels — never hardcode the release name.
- Add a corresponding Helm test in `templates/tests/` for any new Service.

## Security

This chart is scanned weekly by Trivy. If you find a security issue, please report it privately via GitHub's [security advisories](https://github.com/nunoferna/pihole-helm/security/advisories) rather than opening a public issue.
