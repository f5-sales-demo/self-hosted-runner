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

It considers lockfile installs such as `npm ci`, `bun install --frozen-lockfile`, and `pip install -r requirements.txt` job-local. Self-hosted setup actions, floating tool versions, global installs, curl/wget installers, and privileged package installation are rejected unless their tool is supplied by the catalogue.

## Upstream update detection

`Check runner tool updates` runs weekly and on demand. It compares the catalogued, supported upstreams with their official release, registry, or Go-module endpoint and uploads a machine-readable report. An available version or a failed lookup makes the check fail intentionally.

The report is a review signal, not an automated image mutation. Each update must be incorporated through a pull request that changes the exact version, source URL, checksum or Go module sum, catalog entry, and verifier together. This keeps production images immutable while avoiding silent drift.

```bash
python3 scripts/check-tool-updates.py --format json
```

## Promotion sequence

1. Merge a reviewed builder PR. Only the GitHub-hosted publish workflow builds and pushes a candidate.
2. Record the two published digests and use `scripts/verify-promotion.sh` to verify GitHub provenance and the BuildKit SPDX SBOM attestation.
3. On the Ubuntu workstation, run `scripts/preload-image.sh` for each digest. It pulls the digest, proves local image identity, and runs the resident verifier without maintaining a second production build path.
4. Update `docs-control` profile policy with those digests, then run one socketless and one trust-gated container-build pilot. Do not edit existing open issues #1533 or #1580 for this rollout.

See [docs/rollout.md](docs/rollout.md) for the required downstream handoff.
