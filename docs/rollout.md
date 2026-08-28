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

## ARC capacity and compute rollout

1. Confirm the two Canada Central quota requests are approved at 600 or more and that the Terraform plan contains only the Premium ACR, its kubelet `AcrPull` assignment, the compute pool, the socketless 0-30 change, and the 60-minute autoscaler setting.
2. Apply the saved plan. Mirror the approved standard/container-build GHCR digests into ACR and verify byte-identical manifests.
3. Deploy the two cache releases. The socketless release must be Ready on both socketless and compute nodes; the container-build release remains confined to build nodes.
4. Use `scripts/arc-copy-pull-secret.sh` to copy the existing combined GHCR/ACR pull secret into new namespaces. The script rejects a token annotated to expire within 24 hours; rotate first when rejected. Deploy each approved compute scale set at 0-2, then manually run ARC Compatibility before changing ordinary tests/native builds. Route release compilation last.
5. Benchmark the identical xcsh commit on D8 and D16 nodes, with cold and warm image/package caches, five runs for lifecycle-script limits 4, 8, and 16. Keep 8 only when it has the lowest median without peak memory reaching 80% or instability.
6. Burst two xcsh, two enriched-spec, and two provider compute jobs together. Confirm the shared pool never exceeds five compute nodes, each repository stays at two runners, and the sixth request queues cleanly. Then burst 30 socketless and 5 container-build jobs. Prove unique ephemeral pods, exact namespaces, no avoidable pending state, and node scale-down after 60 minutes.
7. Record two complete 06:00-22:00 America/Toronto business-day peak windows before acceptance. Warm assignment p95 must be at most 20 seconds and cold assignment p95 at most 180 seconds.

Rollback is label-first: route compute jobs back to `xcsh-socketless`, restore the last verified GHCR digest references, and set compute maximum capacity to zero. Do not remove the pool or mirror evidence until correctness, security, and latency are stable again.
