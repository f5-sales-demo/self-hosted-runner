# Azure autoscaling runner fleet

This is a non-applying Terraform plan for an Azure-hosted, ephemeral GitHub
Actions runner fleet. It is a Linux-only foundation: the external dispatcher,
GitHub App credential lifecycle, Windows runners, and macOS capacity are
follow-up work. State bootstrap is separate from the runner fleet so no backend
coordinates, state, credentials, registration tokens, or secrets are committed.

## Layout

- `bootstrap/` creates the dedicated private Azure Storage state backend.
- `runner-fleet/` uses an existing Azure Storage backend and declares a runner
  resource group, network isolation, Key Vault, observability, identities, and
  scale-to-zero socketless and container-build VM Scale Sets.

The pools have separate subnets, identities, immutable Azure Compute Gallery
image-version IDs, capacity inputs, and bootstrap profiles. The container-build
pool must retain the trusted Docker admission policy; this Terraform code does
not weaken it.

## Validate without Azure mutation

```sh
terraform -chdir=terraform/bootstrap init -backend=false
terraform -chdir=terraform/bootstrap validate

terraform -chdir=terraform/runner-fleet init -backend=false
terraform -chdir=terraform/runner-fleet validate
```

The CI workflow runs formatting and backend-disabled validation on every pull
request and main-branch push. It cannot apply infrastructure.

## State backend bootstrap

After a reviewed and explicitly authorized bootstrap apply, initialize the
main backend with deployment-system-held values. Keep them outside version
control; the bootstrap module deliberately does not output backend coordinates.

```sh
terraform -chdir=terraform/runner-fleet init \
  -backend-config="resource_group_name=<state-resource-group>" \
  -backend-config="storage_account_name=<state-storage-account>" \
  -backend-config="container_name=tfstate" \
  -backend-config="key=azure-runner-fleet.tfstate" \
  -backend-config="use_azuread_auth=true"
```

Azure AD authentication is mandatory: shared access keys are disabled. Supply
only trusted, stable operator or CI egress CIDRs in `state_allowed_ipv4_cidrs`,
an existing central Log Analytics workspace ID, and every authorized operator
or CI object ID in `terraform_principal_ids`. The module creates one `Storage
Blob Data Contributor` assignment at the state container for each identity.
The container is private, the storage firewall denies every other network, and
Blob read/write/delete logs go to that workspace. Do not commit state, plan
files, backend coordinates, GitHub App credentials, registration tokens,
webhook secrets, or private keys.

## Dispatcher contract

VMSS capacity starts at zero and Terraform creates no Azure Monitor autoscale
setting. A separately reviewed dispatcher owns VMSS count decisions and must
enforce `dispatcher_capacity_limits` as application policy. Terraform does not
claim to make direct VMSS writes mathematically impossible. Before a capacity
change, it verifies repository, exact labels, queue state, and Docker trust.
Each VM registers one ephemeral runner, executes one job, deregisters, and
returns to zero. NSGs allow Internet egress only on HTTPS and deny Azure Load
Balancer, Internet, and virtual-network ingress.

`runner_bootstrap_uri` must point to a credential-free, versioned HTTPS
artifact and `runner_bootstrap_sha256` must be its reviewed SHA-256 digest.
Cloud-init verifies the digest before granting execution permission. Terraform
supplies only immutable image IDs, the artifact URI and digest, and Key
Vault/managed-identity references. It never stores GitHub App credentials,
registration tokens, or backend coordinates.

## Diagnostics

Key Vault `AuditEvent` logs, per-pool VMSS `AllMetrics`, and state Blob
`StorageRead`, `StorageWrite`, and `StorageDelete` logs go to Log Analytics.

## Local inputs

Do not commit Terraform variable files. `*.tfvars` and `*.tfvars.json` are
ignored, and CI rejects either type if it is force-added. Supply all deployment
values through approved secret management, CI variables, or locally created
ignored files. Do not place names, IDs, addresses, image references, public
keys, backend coordinates, credentials, registration tokens, or any other
environment-specific detail in GitHub.
