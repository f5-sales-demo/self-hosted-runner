# Immutable self-hosted runner images

This repository is the sole image authority for the F5 Sales Demo ephemeral GitHub Actions runner fleet. It publishes two Linux/amd64 targets from a digest-pinned Ubuntu 24.04 base:

| Target | Purpose | Docker capability |
| --- | --- | --- |
| `standard` | General repository-scoped self-hosted jobs | No Docker client or socket |
| `container-build` | The existing trust-gated container-build profile | Docker CLI, Buildx, and Compose; no daemon |

Every production reference is an immutable `ghcr.io/f5-sales-demo/self-hosted-runner@sha256:…` digest. Tags are discovery aids only and must never be placed in runner policy.

## What is pinned

- Ubuntu, Node, and Docker CLI base images are digest-pinned in the `Dockerfile`.
- The GitHub Actions runner, GitHub CLI, Go, .NET, PowerShell, AWS CLI, Helm, and Android command-line tools use versioned HTTPS URLs and verified SHA-256 checksums.
- `catalog/tool-catalog.json` records the exact runner-images reference revision, installed tools, setup-action cache entries, sources, and version checks. `scripts/verify-tools.py` is installed in every target and validates the image contract.

## Setup-action cache contract

`/opt/hostedtoolcache` is an immutable image seed and remains read-only at runtime. `docs-control` copies that seed into each ephemeral runner's mounted workspace and exposes the private copy as `RUNNER_TOOL_CACHE`; setup actions can therefore use catalogued cache hits and install a missing version without mutating the image or sharing a host cache. `actions/setup-go` requires the lowercase `go` directory, so the Go seed is `/opt/hostedtoolcache/go/<version>/x64.complete`.

The runtime cache is removed with the ephemeral runner workspace after every job. It is deliberately not a persistent host cache or a third image-authority path.

The locked reference is [`actions/runner-images@8926c75ceb03577c5cc94415743a88f548b781ab`](https://github.com/actions/runner-images/tree/8926c75ceb03577c5cc94415743a88f548b781ab), Ubuntu 24.04 image version `20260810.271.1`. GitHub-hosted runner images are VM images, not a supported Docker base, so this project deliberately builds a container-compatible tool catalogue instead of inheriting an unsupported hosted-runner Dockerfile.

## Local commands

Production image construction and publication are intentionally restricted to the GitHub-hosted `ubuntu-24.04` workflows. The Ubuntu workstation may use local no-cache builds for development validation only, never as a promotion source; production preloading pulls and verifies a published digest:

```bash
scripts/preload-image.sh \
  ghcr.io/f5-sales-demo/self-hosted-runner@sha256:<digest> standard
```

The fleet audit is safe to run from a GitHub-hosted job or from a checkout of the governed repositories:

```bash
python3 scripts/audit-fleet-workflows.py --checkouts-root /path/to/checkouts
```

Its JSON output contains a machine-readable inventory of every Marketplace action used by a self-hosted job, its full-SHA references, and its dependency classification. Every action must be classified as an image-tool setup, runner-runtime consumer, Docker/socket consumer, or workflow-only; unclassified actions fail the audit.

It considers lockfile installs such as `npm ci`, `bun install --frozen-lockfile`, and `pip install -r requirements.txt` job-local. Self-hosted setup actions, floating tool versions, global installs, curl/wget installers, privileged package installation, and Docker/socket actions outside `container-build` are rejected unless their tool is supplied by the catalogue.

## Upstream update detection

`Check runner tool updates` runs weekly and on demand. It compares the catalogued, supported upstreams with their official release, registry, or Go-module endpoint and uploads a machine-readable report. An available version or a failed lookup makes the check fail intentionally.

The report is a review signal, not an automated image mutation. Each update must be incorporated through a pull request that changes the exact version, source URL, checksum or Go module sum, catalog entry, and verifier together. This keeps production images immutable while avoiding silent drift.

```bash
python3 scripts/check-tool-updates.py --format json
```

## Promotion sequence

1. Merge a reviewed builder PR. Only the GitHub-hosted publish workflow builds and pushes a candidate.
2. Record the two published digests and use `scripts/verify-promotion.sh` to verify GitHub provenance.
3. On the Ubuntu workstation, run `scripts/preload-image.sh` for each digest. It pulls the digest, proves local image identity, and runs the resident verifier without maintaining a second production build path.
4. Update `docs-control` profile policy with those digests, then run one socketless and one trust-gated container-build pilot. Do not edit existing open issues #1533 or #1580 for this rollout.

See [docs/rollout.md](docs/rollout.md) for the required downstream handoff.

## AKS execution platform

Stage 1 uses AKS and GitHub Actions Runner Controller. Terraform owns Azure
infrastructure; interactive Helm owns ARC, the repository-scoped scale sets,
and image pre-pullers. The standard image remains socketless. The
container-build image connects only to a privileged, pod-local DinD daemon.
All repository scale sets use zero idle runners. Two shared image-cache
DaemonSets run in `arc-runner-cache`, one per node profile; repository
namespaces do not carry duplicate cache releases.

See [terraform/README.md](terraform/README.md) for the backend, AKS, ARC, and
pilot procedure. Runtime image digests, GitHub App material, Terraform inputs,
plans, state, and kubeconfig are never committed.

Every runner is treated as eligible to execute multi-customer xcsh work. Before
reading its registration token or contacting GitHub, the image queries the live
host kernel and requires Landlock ABI 2 or newer. A missing or blocked syscall,
invalid result, or ABI 1 denies admission with an operator diagnostic. Move the
workload to Ubuntu HWE or another newer kernel/host instead of accepting
scanner-only containment. The check runs inside the runner pod, so it verifies
the kernel that actually governs the job rather than the image build host.

## Self-hosted Renovate

`renovate-system/` is the isolated build context for the organization-owned Renovate bot. The
upstream receipt pins Renovate 44.52.1 by manifest digest; a path-scoped GitHub-hosted workflow
publishes the derived GHCR image with provenance. `scripts/promote-renovate-image.sh` copies that
exact manifest to ACR and writes the lock receipt consumed by `scripts/renovate-deploy.sh`. Tags,
upstream images, digest disagreement, and unlocked runtime references are rejected.

The suspended CronJob uses a PEM-reading init container to validate exact App metadata,
permissions, bot identity, and 39-repository selected scope. It hands a short-lived token through a
memory-backed volume to the main container, which immediately unlinks it; the main container never
mounts the PEM. The workload has no RBAC or service-account token, and its Cilium policy denies
ingress and every egress destination except inspected DNS and the five declared HTTPS hosts.
The image seeds upstream containerbase metadata into a dedicated memory-backed
`/opt/containerbase` volume so lockfile tools can install exact project runtimes without making the
image root writable. CI exercises Node provisioning under that deployed filesystem contract.

Regenerate or verify the single global configuration with:

```bash
scripts/repository-inventory.py
scripts/repository-inventory.py --check
```
