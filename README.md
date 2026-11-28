# Immutable self-hosted runner images

This repository is the sole image authority for the F5 Sales Demo ephemeral
GitHub Actions runner fleet. It publishes three Linux/amd64 targets from
digest-pinned sources:

| Target | Purpose | Docker capability |
| --- | --- | --- |
| `standard` | General scoped jobs | No client or socket |
| `container-build` | Trust-gated builds | CLI tools; no daemon |
| `super-linter-seed` | Verified archive seed | Non-root loader only |

The container-build target includes Docker CLI, Buildx, and Compose. The seed
is a compressed, identity-verified Docker archive for the workflow-pinned
Super-Linter image; Docker CLI is used only by the non-root per-job loader.

Every production reference is an immutable GHCR, ACR, or ECR digest. GHCR is
the publication authority; provider registries are byte-identical deployment
mirrors. Tags are discovery aids only and must never be placed in runner policy.

For a GHCR source, set `GHCR_SOURCE_DOCKER_CONFIG` to an existing Docker
configuration directory when its registry credentials differ from the GitHub
CLI token. The mirror helper retains only GHCR authentication in its temporary
configuration and removes that configuration when it exits. Other credentials
and the original configuration remain unchanged. A configured GHCR credential
helper applies only to GHCR; target-registry login still verifies the target
account. Missing source access fails before copying, without authentication
fallback. The default path continues to use the GitHub CLI token.

## What is pinned

- Ubuntu, Node, and Docker CLI base images are digest-pinned in the
  `Dockerfile`.
- The GitHub Actions runner, GitHub CLI, Go, Zig, .NET, PowerShell, AWS CLI,
  Helm, and Android command-line tools use versioned HTTPS URLs and verified
  SHA-256 checksums.
- `catalog/tool-catalog.json` records the exact runner-images reference
  revision, installed tools, setup-action cache entries, sources, and version
  checks. `scripts/verify-tools.py` is installed in every target and validates
  the image contract.

## Setup-action cache contract

`/opt/hostedtoolcache` is an immutable image seed and remains read-only at
runtime. `docs-control` copies that seed into each ephemeral runner's mounted
workspace and exposes the private copy as `RUNNER_TOOL_CACHE`; setup actions can
therefore use catalogued cache hits and install a missing version without
mutating the image or sharing a host cache. `actions/setup-go` requires the
lowercase `go` directory, so the Go seed is
`/opt/hostedtoolcache/go/<version>/x64.complete`.

The runtime cache is removed with the ephemeral runner workspace after every
job. It is deliberately not a persistent host cache or a third image-authority
path.

The container-build profile also loads an immutable Super-Linter archive into
its private DinD daemon before runner startup. The seed image is pre-pulled on
container-build nodes, but `/var/lib/docker` remains a per-job 100 GiB
`emptyDir`. A digest, archive, or loaded-image mismatch removes the rejected
image and records a cold-pull fallback; it never turns shared writable Docker
state into a cache.

The locked reference is
[`actions/runner-images@8926c75ceb03577c5cc94415743a88f548b781ab`](https://github.com/actions/runner-images/tree/8926c75ceb03577c5cc94415743a88f548b781ab),
Ubuntu 24.04 image version `20260810.271.1`. GitHub-hosted runner images are VM
images, not a supported Docker base, so this project deliberately builds a
container-compatible tool catalogue instead of inheriting an unsupported
hosted-runner Dockerfile.

## Local commands

Production image construction and publication are intentionally restricted to
the GitHub-hosted `ubuntu-24.04` workflows. The Ubuntu workstation may use local
no-cache builds for development validation only, never as a promotion source;
production preloading pulls and verifies a published digest:

```bash
scripts/preload-image.sh \
  ghcr.io/f5-sales-demo/self-hosted-runner@sha256:<digest> standard
```

The fleet audit is safe to run from a GitHub-hosted job or from a checkout of
the governed repositories:

```bash
python3 scripts/audit-fleet-workflows.py --checkouts-root /path/to/checkouts
```

Its JSON output contains a machine-readable inventory of every Marketplace
action used by a self-hosted job, its full-SHA references, and its dependency
classification. Every action must be classified as an image-tool setup,
runner-runtime consumer, Docker/socket consumer, or workflow-only; unclassified
actions fail the audit.

It considers lockfile installs such as `npm ci`, `bun install
--frozen-lockfile`, and `pip install -r requirements.txt` job-local. Self-hosted
setup actions, floating tool versions, global installs, curl/wget installers,
privileged package installation, and Docker/socket actions outside
`container-build` are rejected unless their tool is supplied by the catalogue.

## Original observer control

`docker-action-profile control` operates on the original observation recorded
beside its profile. It checks the owning process, workspace, output, phase,
image and reviewed identities before accepting control. It preserves the
original clock and never submits another observation.

Use the same values that launched the observer:

```bash
control=(
  --name super-linter --output "$profile_dir/profile.json"
  --image "$SUPER_LINTER_IMAGE"
  --expected-digest "$SUPER_LINTER_DIGEST"
  --expected-action-commit "$SUPER_LINTER_ACTION_COMMIT"
  --expected-manifest-digest "$SUPER_LINTER_MANIFEST_DIGEST"
)
docker-action-profile control wait-ready "${control[@]}" --wait 10
docker-action-profile control inspect "${control[@]}"
docker-action-profile control wait-complete "${control[@]}" --wait 20
```

`cancel` writes one exclusive, ownership-bound cancellation request. Repeating
that same request does not rewrite it or reset the deadline. The observer and
its commands close naturally; no signal is sent. `wait-complete` accepts a
successful terminal only after every original process is absent. A changed,
stopped, truncated, late or unresolved capture stays incomplete and retains
its evidence and locks. Each wait is bounded to at most sixty seconds.

## Upstream update detection

`Check runner tool updates` runs weekly and on demand. It compares the
catalogued, supported upstreams with their official release, registry, or
Go-module endpoint and uploads a machine-readable report. An available version
or a failed lookup makes the check fail intentionally.

The report is a review signal, not an automated image mutation. Each update must
be incorporated through a pull request that changes the exact version, source
URL, checksum or Go module sum, catalog entry, and verifier together. This keeps
production images immutable while avoiding silent drift.

```bash
python3 scripts/check-tool-updates.py --format json
```

## Promotion sequence

1. Merge a reviewed builder PR. Only the GitHub-hosted publish workflow builds
   and pushes a candidate.
2. Record the three published digests. Run `scripts/verify-promotion.sh` to
   verify exact-source GitHub provenance and each image's registry-hosted SPDX
   SBOM. The publisher uses a digest-pinned BuildKit scanner; it does not upload
   size-limited SPDX payloads through GitHub attestation actions.
3. On the Ubuntu workstation, run `scripts/preload-image.sh` for the standard
   and container-build digests. The seed digest is pulled by the container-build
   pre-pull DaemonSet and its archive/image identities are verified per job.
4. Update `docs-control` profile policy with those digests, then run one
   socketless and one trust-gated container-build pilot. Do not edit existing
   open issues #1533 or #1580 for this rollout.

See [docs/rollout.md](docs/rollout.md) for the required downstream handoff.

## Portable execution platform

Independent Terraform roots manage Azure AKS and AWS EKS without shared state.
Interactive Helm owns ARC, the repository-scoped scale sets, and image
pre-pullers. The standard image remains socketless. The
container-build image connects only to a privileged, pod-local DinD daemon.
`scripts/arc-deploy.sh` requires `SUPER_LINTER_SEED_IMAGE` alongside the two
runner images. ACR/ECR deployments also require
`SUPER_LINTER_SEED_SOURCE_IMAGE` and byte-verify it against the mirrored digest
before rendering any runner. The seed must use the same registry as the
container-build image so the pod has one explicit registry trust path.
Repository scale sets use zero idle runners except the provider stable compute
profile, which reserves one idle runner. Two shared image-cache
DaemonSets run in `arc-runner-cache`, one per node profile; repository
namespaces do not carry duplicate cache releases.

See [docs/provider-toolchain-canary.md](docs/provider-toolchain-canary.md) for
provider-only qualification of new immutable tool defaults on the existing
compute pool. The canary is disabled in default deployment modes.

See [terraform/README.md](terraform/README.md) for cloud selection and the
provider-specific backend, cluster, ARC, and pilot procedures. Runtime image
digests, GitHub App material, Terraform inputs,
plans, state, and kubeconfig are never committed.

Every runner is treated as eligible to execute multi-customer xcsh work. Before
reading its registration token or contacting GitHub, the image queries the live
host kernel and requires Landlock ABI 2 or newer. A missing or blocked syscall,
invalid result, or ABI 1 denies admission with an operator diagnostic. Move the
workload to Ubuntu HWE or another newer kernel/host instead of accepting
scanner-only containment. The check runs inside the runner pod, so it verifies
the kernel that actually governs the job rather than the image build host.

## Self-hosted Renovate

`renovate-system/` is the isolated build context for the organization-owned
Renovate bot. The
upstream receipt records the qualified current stable Renovate release and
its manifest digest in `renovate-system/image-source.json`; a path-scoped
GitHub-hosted workflow
publishes the derived GHCR image with provenance.
`scripts/promote-renovate-image.sh` copies that
exact manifest to ACR and writes the lock receipt consumed by
`scripts/renovate-deploy.sh`. Tags,
upstream images, digest disagreement, and unlocked runtime references are
rejected.

The active CronJob runs at `20 5 * * *` in `America/Toronto` and uses a
PEM-reading init container
to validate exact App metadata,
permissions, bot identity, and 46-repository selected scope. It hands a
short-lived token through a
memory-backed volume to the main container, which immediately unlinks it; the
main container never
mounts the PEM. The workload has no RBAC or service-account token, and its
Cilium policy denies
ingress and every egress destination except inspected DNS and the five declared
HTTPS hosts.
The image seeds upstream containerbase metadata into a dedicated memory-backed
`/opt/containerbase` volume so lockfile tools can install exact project runtimes
without making the
image root writable. A bounded 2 GiB memory-backed `/cache` retains
package-manager and
containerbase downloads across the serial fleet run, while a separate bounded 4
GiB memory-backed
`/tmp` provides executable transient extraction space. CI exercises Node
provisioning under that
deployed filesystem contract; production probes exercise Rust with a
representative prefilled
download cache.

All 46 governed repositories use zero release age for every update manager.
Current stable releases are immediately eligible. Package updates have no
historical uv or Babel/Rolldown holds; required review, passing CI and immutable
artifact verification still apply. Qualify the current runtime and dependency
graph before installing each update.

Regenerate or verify the single global configuration with:

```bash
scripts/repository-inventory.py
scripts/repository-inventory.py --check
```
