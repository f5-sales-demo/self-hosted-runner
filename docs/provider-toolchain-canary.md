# Provider toolchain canary

The provider toolchain canary qualifies new immutable tool defaults before the
provider's stable workflows adopt them. It reuses the existing enabled `compute`
pool with the same socketless security and resource contract. Its distinct
runner label is `terraform-provider-xcsh-toolchain-canary`, with zero idle runners
and a maximum of one runner.

## Prerequisites

Merge and verify the runner image source through the existing publication and
provenance workflow. Record the approved standard image digest and full source
commit in the private rollout receipt. Coordinate the exact provider-only route
and runtime attestation through `docs-control` before assigning provider jobs.
Existing stable runners keep their current tool defaults until the new provider
source passes canary CI and the coordinated stable promotion is qualified.

Verify the destination cluster, enabled physical compute pool, namespace,
resource inventory and runtime identity before deployment. The canary namespace
is `arc-runners-terraform-provider-xcsh-compute-toolchain-canary`; its Helm
release is `terraform-provider-xcsh-compute-toolchain-canary`. The namespace must
already contain the authorized `arc-github-app` and `ghcr-pull` secrets. The
command does not create secrets or a node pool.

## Deployment

Use the protected cluster administrator kubeconfig with mode `0400` or `0600`.
Set `TOOLCHAIN_CANARY_IMAGE` to the approved immutable
`ghcr.io/f5-sales-demo/self-hosted-runner@sha256:<digest>` reference and
`TOOLCHAIN_CANARY_SOURCE_SHA` to its full verified merged source commit.

From the verified Ubuntu checkout, run:

```sh
scripts/arc-deploy.sh arc/repositories/terraform-provider-xcsh.yaml toolchain-canary
```

The dedicated mode verifies image provenance and the pinned scale-set chart,
upgrades only the canary release, and checks its ARC listener reconciliation.
It refuses a foreign repository, disabled compute pool, mutable image, invalid
source identity or failed provenance. Default deployment modes omit the canary.

Retain live image, tool-version, runner/pool identity and source-CI evidence.
Deployment completion alone does not qualify the provider's new toolchain.
After the provider source and stable image identities converge, remove the
canary through a separately reviewed inventory and retain the closure receipt.
