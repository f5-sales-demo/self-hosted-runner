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
| socketless | Standard_D8ads_v5 | 1-20 | ephemeral | socketless runners |
| build | Standard_D16ads_v5 | 0-5 | ephemeral | DinD runners |

Labels and NoSchedule taints enforce profile placement. Do not substitute
another version, region, zone, SKU, disk type, or capacity when preflight fails.

## Remote state

The separately bootstrapped Azure Storage backend uses HTTPS, a private
container, versioning, soft delete, and a deny-by-default firewall. It permits
shared-key access because the operator lacks Blob data-plane and
role-assignment permissions. Terraform creates no role assignments.

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
inspect it completely. Reject unexpected deletes or replacements, explicit
role assignments, public node IPs, secrets, or anything outside the AKS graph.
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

The xcsh socketless and container-build scale sets are capped at 10 and 3
respectively, both with zero idle runners. The original self-hosted-runner
configuration retains its 20 and 5 limits. The socketless node pool stays warm;
the build pool scales from and back to zero.
