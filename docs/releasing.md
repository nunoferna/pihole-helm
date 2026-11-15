# Release operations

Review and merge the tested change to `main` first. Publishing is a separate, manually invoked **Release Charts** workflow. Do not push a release tag or rebuild a package by hand to bypass the gate.

The main-push **Lint and Test Charts** run must complete successfully, including:

- Contract, semantic-version, real provenance/tamper and security-exception regressions.
- Exact chart lint and one canonical reproducible `.tgz` with a source SHA/package digest manifest.
- Runtime checks of that archive on Kubernetes 1.35.8/1.36.4/1.37.0, Helm 3.22.1/4.3.2 and amd64/arm64.
- Blocking HIGH/CRITICAL scans of rendered manifests and both runtime images on each architecture; empty/skipped/error scans fail.
- A disposable three-node VIP failure, bounded synthetic load, TLS validation and encrypted fresh-namespace recovery fixture.

The archive artifact is named `tested-chart` and retained for 14 days. Each matrix run also uploads evidence. The matrix's kind NetworkPolicy objects do not prove CNI enforcement; a real-CNI profile remains a separate deployment acceptance check. Local reports record the combinations actually run; configuring CI does not mean it has executed.

## Promote the tested artifact

Configure repository secrets `PGP_PRIVATE_KEY` and `PGP_PASSPHRASE` for the intended release signing identity. Enable HTTPS GitHub Pages from the `gh-pages` branch root and allow the workflow token to write contents/packages/Pages. Retain and independently verify the public signing-key fingerprint. The workflow publishes the public key alongside the chart; fetching a key alone does not establish trust in its identity.

Invoke **Release Charts** with the successful main-push run's numeric `tested-run-id`. It checks the run's repository, workflow path, branch, event, conclusion and source SHA. It downloads that run's archive and verifies the digest/source manifest. Signing produces a Helm `.prov` for the existing bytes and verifies it locally; it never repackages the chart.

`scripts/publish-chart.py` previews by default. The release workflow explicitly selects `--execute` after verification. It checks each destination separately:

| Destination | Retry behavior |
| --- | --- |
| GitHub release | Verify the existing tag's source commit, release type and existing asset bytes; upload only missing assets. Reuse an existing signature only after trusted-key verification against the exact archive. |
| GHCR OCI | Pull/check any existing chart, publish only if absent, then verify exact chart bytes and Helm provenance after the round trip. |
| Pages Helm index | Reject conflicting same-version digests/URLs; add a missing entry with a leased update, explicitly request a Pages build and verify the served HTTPS index. |

The explicit build request is necessary because `GITHUB_TOKEN` commits do not trigger Pages builds automatically. See [GitHub's publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site) and [Pages build API](https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build). A retry checks delivery even if the branch already contains the right index.

A release tag never causes missing OCI or Pages work to be skipped. Rerun the workflow with the **same tested run ID** after a partial failure. A mismatched existing chart, signature, tag or index entry stops publication; investigate it rather than overwriting a released version. If artifacts have expired, run full certification again for the intended unchanged source commit and use that successful main-push run; never substitute newly built untested bytes.

Semver prereleases are published as GitHub prereleases and do not become Latest. `scripts/bump-version.py` distinguishes prerelease increments from an explicit stable promotion; patch/minor/major transitions refuse to accidentally promote a prerelease. The bump workflow opens a PR and cannot bypass the full release checks.

## Local release checks

```sh
python3 -m unittest discover -s scripts/validation -p 'test_*.py' -v
python3 scripts/package-chart.py --output /tmp/pihole-package
python3 scripts/package-chart.py --output /tmp/pihole-package --verify-only
```

Dirty local sources are identified in the manifest and cannot be signed/published by these tools. The signing test uses a disposable GPG identity and temporary Git repository. An optional `PIHOLE_TEST_OCI_BASE=oci://localhost:PORT/charts` runs real push/pull/resume/tamper tests against a disposable local registry; plaintext registry transport is restricted to explicit loopback fixtures. Production OCI publication uses TLS.

The checkout version alone is not evidence of a published release. Keep runtime/security evidence with the reviewed change, inspect the final CI run, then invoke the concrete promotion workflow.
