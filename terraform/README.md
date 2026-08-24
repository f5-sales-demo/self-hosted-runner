# Azure autoscaling runner fleet

This is a non-applying Terraform plan for an Azure-hosted, ephemeral GitHub
Actions runner fleet. State bootstrap is separate from the runner fleet so no
backend coordinates, state, credentials, or secrets are committed.

## Layout

- `bootstrap/` creates the dedicated private Azure Storage state backend.
- `runner-fleet/` uses an existing Azure Storage backend and declares a runner
  resource group, network isolation, Key Vault, observability, identities, and
  scale-to-zero socketless and container-build VM Scale Sets.

The pools have separate subnets, identities, capacity limits, and bootstrap
profiles. The container-build pool must retain the trusted Docker admission
policy; this Terraform code does not weaken it.

## Validate without Azure mutation

```sh
terraform -chdir=terraform/bootstrap init -backend=false
terraform -chdir=terraform/bootstrap validate

terraform -chdir=terraform/runner-fleet init -backend=false
terraform -chdir=terraform/runner-fleet validate
```

## State backend bootstrap

After a reviewed and explicitly authorized bootstrap apply, initialize the
main backend with values from the bootstrap output. Keep these values outside
version control.

```sh
terraform -chdir=terraform/runner-fleet init \
  -backend-config="resource_group_name=<state-resource-group>" \
  -backend-config="storage_account_name=<state-storage-account>" \
  -backend-config="container_name=tfstate" \
  -backend-config="key=azure-runner-fleet.tfstate" \
  -backend-config="use_azuread_auth=true"
```

Azure AD authentication is mandatory: shared access keys are disabled. Supply
only trusted, stable operator or CI egress CIDRs for the bootstrap
`state_allowed_ipv4_cidrs` input, and grant each Terraform identity `Storage
Blob Data Contributor`. The container is private and the storage firewall
denies every other network. Do not commit state, plan files, backend
coordinates, GitHub App credentials, webhook secrets, or private keys.

## Dispatcher contract

VMSS capacity starts at zero. The autoscale settings deliberately contain no
scale-out rules: a separately reviewed dispatcher is the only component
allowed to raise capacity. Before doing so, it must verify the repository,
exact labels, queue state, Docker trust gate, and configured capacity limit.
Each VM must register one ephemeral runner, execute one job, deregister, and
return to zero capacity. The NSGs allow Internet egress only on HTTPS and deny
both Internet and virtual-network ingress.

`runner_bootstrap_uri` must point to a credential-free, versioned HTTPS
artifact and `runner_bootstrap_sha256` must be its reviewed SHA-256 digest.
Cloud-init verifies the digest before granting execution permission. The
bootstrap receives no long-lived GitHub credential from Terraform.
