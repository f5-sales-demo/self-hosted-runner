# Fleet rollout handoff

This repository publishes image artifacts only. `docs-control` continues to own repository policy, per-repository labels, registration, diagnostics retention, profile isolation, and the only Docker-socket trust gate.

## Required first promotion

1. Merge the builder PR and let the GitHub-hosted `publish.yml` workflow emit a `standard` and a `container-build` digest.
2. Run `scripts/verify-promotion.sh` for both references with the merged source commit. This requires GHCR login with `read:packages` and rejects self-hosted provenance.
3. On the Ubuntu workstation, run `scripts/preload-image.sh` for the exact digest/profile pair. It pulls; it never builds.
4. In a fresh `docs-control` issue and isolated worktree, replace every old `actions-runner` image reference with the `standard` digest and the old `actions-runner-container` reference with the `container-build` digest. Retire the old `runner-images/Containerfile` publisher only after the pilot succeeds.

## Pilot acceptance

- A standard profile job completes with `verify-runner-tools standard`, has no `docker` executable, and deregisters after one job.
- A trust-gated `container-build` job completes with `verify-runner-tools container-build`, receives the socket only through the existing controller route, and retains diagnostics exactly as before.
- The policy remains digest-only, repository-scoped labels are unchanged, and documented GitHub-hosted native, platform, and ARM exceptions remain explicit.

## Migration gate

Run `python3 scripts/audit-fleet-workflows.py --github --ref main` before each batch. The audit is deliberately fail-closed for self-hosted jobs that use floating setup-action versions, setup/download actions outside the catalogue, direct installers, or privileged package installation. Lockfile installs remain job-local and are reported as informational.

Migrate managed workflow templates first, then remaining governed repositories in reviewable batches. Do not call the fleet complete until all 39 audit clean and their required checks are green.
