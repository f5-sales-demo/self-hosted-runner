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

1. Confirm the two Canada Central quota requests are approved at 600 or more and inspect the complete Terraform plan. The dedicated Premium ACR intentionally permits anonymous pull for every current and future repository; pushes remain authenticated and the admin account remains disabled. Anonymous clients share one ACR throttling identity, so include pull-rate monitoring in the rollout.
2. Apply the saved plan. Mirror the approved standard/container-build GHCR digests into ACR and verify byte-identical manifests.
3. Deploy the two cache releases. The socketless release must be Ready on both socketless and compute nodes; the container-build release remains confined to build nodes.
4. Use `scripts/arc-copy-pull-secret.sh arc-runner-cache arc/repositories/*.yaml` to reconcile the private GHCR credential into every ARC namespace. The source and every copied `ghcr-pull` secret must contain exactly `ghcr.io`; ACR pulls are anonymous. Deploy each approved compute scale set at 0-2, then manually run ARC Compatibility before changing ordinary tests/native builds. Route release compilation last.
5. Benchmark the identical xcsh commit on D8 and D16 nodes, with cold and warm image/package caches, five runs for lifecycle-script limits 4, 8, and 16. Keep 8 only when it has the lowest median without peak memory reaching 80% or instability.
6. Burst two xcsh, two enriched-spec, and two provider compute jobs together. Confirm the shared pool never exceeds five compute nodes, each repository stays at two runners, and the sixth request queues cleanly. Then burst 30 socketless and 5 container-build jobs. Prove unique ephemeral pods, exact namespaces, no avoidable pending state, and node scale-down after 60 minutes.
7. Record two complete 06:00-22:00 America/Toronto business-day peak windows before acceptance. Warm assignment p95 must be at most 20 seconds and cold assignment p95 at most 180 seconds.

Rollback is label-first: route compute jobs back to `xcsh-socketless`, restore the last verified GHCR digest references, and set compute maximum capacity to zero. Do not remove the pool or mirror evidence until correctness, security, and latency are stable again.

## Renovate security handoff

1. After the source PR merges, wait for `Publish derived Renovate image`, verify its provenance, and
   promote its immutable GHCR digest with `scripts/promote-renovate-image.sh`. Commit the generated
   image lock plus the non-secret App ID, installation ID, bot login, and numeric bot ID in a second
   linked PR. Never commit the PEM or installation token.
2. The organization owner creates `f5-renovate-aks` with webhooks and events disabled and exactly
   checks-write, commit-statuses-write, metadata-read, contents-write, pull-requests-write, and
   workflows-write, then selects the exact 39-repository catalog. Checks and commit statuses are
   required for the release-age and observed-CI gates. The token init helper verifies App metadata,
   permissions, selected scope, bot identity, token lifetime, and repository equality.
3. Stream the PEM into `renovate-system/renovate-github-app`; the Secret must contain only
   `private-key.pem`. After verified new scope, disable and uninstall the hosted Renovate App.
4. Run `scripts/renovate-deploy.sh renovate-system/image-lock.json`. It re-verifies byte-identical
   GHCR/ACR manifests, the one-key App Secret, suspended chart, exact anonymous ACR digest, and
   socketless pre-puller. The CronJob has no image pull secret; the pre-puller uses only
   `ghcr-pull` for its private GHCR runner image. Confirm there are no Role or RoleBinding objects
   before unsuspending. Keep the root filesystem read-only; the main container's scoped,
   memory-backed `/opt/containerbase` volume is the only executable tool-state surface and is
   seeded from the immutable image before Renovate starts.
   The Renovate container requests 4 GiB and is capped at 8 GiB. That limit covers Renovate and
   package-manager working memory plus cgroup-charged pages in the memory-backed work, cache, tmp,
   and containerbase volumes. Treat any OOM kill as a failed production gate: keep the CronJob
   suspended, retire partial PRs and branches, and repair the resource contract before retrying.
5. Before release, create one Job from the suspended CronJob and wait for success. The clean-break
   configuration intentionally has no manager-level time schedules, so the production run must
   immediately exercise npm and GitHub Actions across all 39 repositories without overrides. Logs
   must contain the 39-scope receipt and no token/key material. Confirm Renovate observes passing
   pull-request checks before squash-merging eligible minor/patch PRs, while a natural or
   temporary-branch major remains manual. Platform-native automerge stays disabled because the
   fleet does not enforce required status checks and GitHub could otherwise merge before CI starts.
   The seven-day release-age gate remains strict, but the self-hosted administrator configuration
   forces both immediate PR creation and Renovate-managed automerge after that gate passes. Fleet CI starts
   on `pull_request`, so a package rule merged into grouped branch configuration cannot be allowed
   to leave an eligible update as a bare branch with indefinitely pending checks.

Renovate rollback is intentionally limited to suspending the CronJob and affected pre-pull
workloads, disabling anonymous ACR pull, and disabling the new App. No token or role-assignment
fallback is retained. Dependabot and the hosted Renovate installation are not restored.
