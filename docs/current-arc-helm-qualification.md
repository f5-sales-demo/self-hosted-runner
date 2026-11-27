# Current ARC and Helm qualification

Prerelease development uses the latest stable releases immediately. There is no
seven-day eligibility window or compatibility hold. Refresh official release
metadata before selecting a successor. Version checks and digests identify the
bytes qualified in an attempt; new releases require fresh qualification.

The current baseline is [Helm 4.3.2][helm] and [ARC 0.15.1][arc]. Helm is acquired
from its official archive with a verified SHA-256 checksum. Both chart OCI
manifests and the multi-platform controller image manifest were independently
verified. Terraform validation uses current stable 1.17.2.

Helm 4 requires a named `postrenderer/v1` plugin. The repository supplies
`arc-controller-image` through `HELM_PLUGINS`, without installation hooks or
changes to the user's plugin directory. Its Python executable rejects missing,
stale, ambiguous or incomplete controller identities before emitting output.
It rewrites both the manager image and its image environment value to the same
verified digest.

`GHCR_SOURCE_DOCKER_CONFIG` explicitly selects existing source credentials for
chart pulls. The shared helper writes only GHCR authentication or its scoped
credential helper into the caller's temporary Helm registry configuration.
Malformed, multiple-document or unavailable explicit authentication fails
without falling back. Original credential files are preserved.

ARC documents a string `githubConfigSecret` for an existing Kubernetes secret.
Its chart defaults use a map, which makes Helm report a coalescing warning for
that documented string override. The original warning is retained in private
qualification evidence. Source inspection confirms that this mode references
the existing secret and omits a generated credential-bearing Secret. Do not
replace it with inline credentials or suppress the diagnostic.

Focused tests include real Helm 4 execution on the official controller chart.
All repository scale sets and shared charts must render successfully, preserving
labels, isolation and resource bounds. Source tests do not establish live
acceptance: independently review existing releases before any deployment, then
verify scoped pilots, one-job deregistration, diagnostics and owned cleanup.
Do not force resource ownership or server-side apply conflicts.

[helm]: https://github.com/helm/helm/releases/tag/v4.3.2
[arc]: https://github.com/actions/actions-runner-controller/releases/tag/gha-runner-scale-set-0.15.1
