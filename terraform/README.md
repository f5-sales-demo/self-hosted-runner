# AKS ephemeral runner platform

Terraform manages Azure infrastructure only. Interactive Helm manages ARC,
runner scale sets, and image pre-pullers after the cluster exists.

## Architecture

The runner root creates a dedicated resource group, AKS cluster, and Log
Analytics workspace. AKS uses Azure CNI Overlay with Cilium, a public API
restricted to operator CIDRs, no public node IPs, Container Insights, and
control-plane diagnostics.

| Pool | SKU | Autoscaling | OS disk | Workload |
| --- | --- | --- | --- | --- |
| system | Standard_D4as_v5 | 1-3 | managed | AKS, ARC controller, listeners |
| socketless | Standard_D8ads_v5 | 0-30 | ephemeral | socketless runners |
| compute | Standard_D16ads_v5 | 0-5 | ephemeral | CPU-heavy socketless xcsh runners |
| build | Standard_D16ads_v5 | 0-5 | ephemeral | DinD runners |

Labels and NoSchedule taints enforce profile placement. Do not substitute
another version, region, zone, SKU, disk type, or capacity when preflight fails.

## Remote state

The separately bootstrapped Azure Storage backend uses HTTPS, a private
container, versioning, soft delete, and a deny-by-default firewall. It permits
shared-key access because the operator lacks Blob data-plane and
role-assignment permissions. Terraform creates no workload, operator, or
secret-management role assignments.

Copy each backend.hcl.example to its ignored backend.hcl peer. Derive the key
only inside the interactive shell running Terraform:

    export ARM_ACCESS_KEY="$(az storage account keys list       --resource-group "$STATE_RESOURCE_GROUP"       --account-name "$STATE_STORAGE_ACCOUNT"       --query '[0].value' -o tsv)"
    terraform -chdir=terraform/runner-fleet init -reconfigure       -backend-config=backend.hcl
    unset ARM_ACCESS_KEY

Never place that key in a backend file, history, process argument, log, or Git.

## Validate and apply

Create ignored terraform/runner-fleet/terraform.tfvars with the deployment
names, operator CIDRs, pod CIDR, service CIDR, and DNS service IP. Then run:

    terraform fmt -check -recursive terraform
    terraform -chdir=terraform/bootstrap init -backend=false
    terraform -chdir=terraform/bootstrap validate
    terraform -chdir=terraform/runner-fleet init -backend=false
    terraform -chdir=terraform/runner-fleet validate
    scripts/validate-arc.sh arc/repositories/self-hosted-runner.yaml arc/repositories/xcsh.yaml
    scripts/check-committed-artifacts.sh

Initialize the real backend, save a binary plan in the ignored worktree, and
inspect it completely. Reject unexpected deletes or replacements, role
assignments, public node IPs, secrets, or anything outside the AKS graph.
Apply only that saved plan and remove it immediately.

Obtain admin credentials into an ignored mode-0600 kubeconfig on the Ubuntu
workstation with az aks get-credentials --admin --file.

## ARC

ARC 0.14.2 is installed by scripts/arc-deploy.sh. The helper verifies the two
OCI chart digests, pins the controller image through a Helm post-renderer, and
accepts only digest-addressed GHCR runner images. The build profile has a
pinned DinD image, Unix socket, and emptyDir work and layer stores. The
socketless profile exposes neither the Docker CLI nor a Docker socket.

Every runner operation requires one validated configuration from
`arc/repositories/`. First export `KUBECONFIG` and install the shared controller:

    scripts/arc-deploy.sh arc/repositories/self-hosted-runner.yaml controller

Create or install a repository-scoped GitHub App with Administration read/write
and Metadata read-only. Keep its IDs and private key outside Git. Export
`GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and
`GITHUB_APP_PRIVATE_KEY_FILE`, then create the selected repository secrets:

    scripts/arc-github-app-secret.sh arc/repositories/xcsh.yaml

If the GHCR package is private, supply a dedicated read-only package credential
through `GHCR_USERNAME` and `GHCR_TOKEN`, then run:

    scripts/arc-ghcr-pull-secret.sh arc/repositories/xcsh.yaml

Do not reuse a broad operator token. Alternatively, an operator may copy the
existing secrets server-side into the configured namespaces after verifying the
source and destination namespaces; never print or persist the secret payloads.

Finally export `SOCKETLESS_IMAGE` and `CONTAINER_BUILD_IMAGE` as immutable
references and deploy the xcsh scale sets and pre-pullers:

    scripts/arc-deploy.sh arc/repositories/xcsh.yaml runners

The xcsh socketless, compute, and container-build scale sets are capped at 10, 5, and 3 respectively, all with zero idle runners. The original self-hosted-runner configuration retains its 20 and 5 limits. Every worker pool scales to zero; after demand drains, the autoscaler retains nodes for 60 minutes.

Validate the complete repository set together before deployment:

    scripts/validate-arc.sh arc/repositories/*.yaml

The documentation cohort uses twelve zero-idle, repository-scoped scale sets
for docs, docs-builder, docs-theme, i18n-core, starlight-llms-txt, and
docs-icons. Its namespaces and Helm releases are unique, while repository scope
allows the cohort to share the workflow labels docs-socketless and
docs-container-build.

## Capacity evidence and image mirror

Do not raise node-pool limits until both Canada Central `standardDADSv5Family` and total regional `cores` quotas are at least 600. The maximum 30/5/5 worker fleet plus three system nodes consumes 412 vCPUs, leaving more than 20% headroom at that quota.

The Premium `f5salesdemoarcca` registry is a deployment mirror; GHCR remains
the publication authority. Anonymous pull is intentionally enabled for the
entire registry, so `renovate`, `self-hosted-runner`, and every future ACR
repository are publicly readable without credentials. Push remains
authenticated and the admin account remains disabled. Azure treats all
anonymous clients as one throttling identity, so monitor registry limits during
bursts. See the Azure documentation for
[anonymous pull](https://learn.microsoft.com/azure/container-registry/anonymous-pull-access)
and [ACR limits](https://learn.microsoft.com/azure/container-registry/container-registry-skus).
Copy and verify each approved source digest, then deploy only the returned equal
digest:

    scripts/mirror-runner-image.sh copy ghcr.io/f5-sales-demo/self-hosted-runner@sha256:<digest>

For ACR deployment, pass the two ACR digest references in `SOCKETLESS_IMAGE` and `CONTAINER_BUILD_IMAGE`, and their equal GHCR references in `SOCKETLESS_SOURCE_IMAGE` and `CONTAINER_BUILD_SOURCE_IMAGE`. `arc-deploy.sh` refuses an ACR deployment unless both manifests are byte-identical. Tags are never accepted. The `ghcr-pull` Kubernetes secret contains exactly the private `ghcr.io` credential; it is not used for ACR.

Capture a 30-day GitHub baseline and the live Kubernetes scheduling/metrics state from the protected workstation kubeconfig:

    scripts/arc-capacity.py collect --repository f5-sales-demo/xcsh --days 30 --output arc-capacity.json

`runner-profile --name <phase> --output <file> -- <command>` records only approved identity fields and cgroup-v2 counters; it never records command arguments, environment values, credentials, or payloads. Jobs upload uniquely named `workload-profile-*` artifacts for 30 days. The capacity collector downloads those artifacts, validates schema version 1, aggregates phase medians/p95/memory/stability, and emits candidate comparisons only after five digest-matched pairs. Dependency wait (`workflow created` to `job created`) is reported separately from runnable assignment (`job created` to `job started`). Post-migration cutoffs exclude legacy-label history.

The checked-in policy defines the 06:00-22:00 America/Toronto service window, warm (20-second p95) and cold (180-second p95) targets, two consecutive five-minute breach rule, ten-minute job wait, two-minute saturated-pool rule, 20% quota headroom, and deterministic repository cap formula. A start is warm only when a schedulable Ready node of the requested profile existed when the job entered the queue.
