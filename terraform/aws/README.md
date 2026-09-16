# AWS EKS runner platform

This stack is independent from Azure. It uses the normal AWS SDK credential
chain and a local SSO session; no static access key, service principal,
GitHub OIDC apply workflow, or new operator role is created.

## Fixed architecture

- Account identity is explicit and the region is fixed at `us-east-1`.
- `10.42.0.0/16` is split across public NAT and private EKS subnets in
  `us-east-1a`, `us-east-1b`, and `us-east-1c`, with one NAT gateway per AZ.
- EKS 1.35 uses restricted public plus private API access, AL2023 release
  `1.35.7-20260911`, encrypted 128-GiB gp3 roots, IMDSv2, On-Demand capacity,
  API-only access entries, KMS secret encryption, and 30-day control-plane logs.
- The enabled capacity is 492 vCPUs including system nodes and requires a
  615-vCPU standard On-Demand quota. The disabled `c6a.8xlarge` density pool
  raises those values to 652 and 815 when explicitly enabled.
- ECR repositories use immutable tags, scan on push, KMS encryption, and the
  node role's read-only ECR permissions.

## Bootstrap and state migration

Create ignored `terraform.tfvars` files in both roots. Do not create the
bootstrap `backend.hcl` until its local apply has created the state bucket and
KMS key. Backend files contain identifiers only. Never add credentials to
Terraform files.

Bootstrap starts with local mode-0600 state:

```sh
umask 077
export AWS_ACCOUNT_ID=123456789012 AWS_REGION=us-east-1
export RUNNER_PLATFORM_STACK=bootstrap
scripts/runner-platform.sh aws plan
scripts/runner-platform.sh aws show
scripts/runner-platform.sh aws apply
chmod 0600 terraform/aws/bootstrap/terraform.tfstate
cp terraform/aws/bootstrap/backend.hcl.example terraform/aws/bootstrap/backend.hcl
# Replace the bucket and KMS identifiers using the protected
# .plans/aws-bootstrap.outputs.json written by the apply command.
MIGRATE_STATE=yes scripts/runner-platform.sh aws init
```

The runner helper temporarily removes the tracked S3 backend declaration from
Terraform's input only while the bootstrap backend configuration is absent, so
the first saved plan and apply genuinely use local state. Confirm the migrated
key is exactly `aws/bootstrap.tfstate`. Then create the fleet `backend.hcl`;
its key is exactly `aws/runner-fleet.tfstate`.

## Reviewed saved-plan deployment

Request and wait for Elastic IP quota 8 before fleet planning. The preflight
also requires 615 standard On-Demand vCPUs, three unused EIP slots, all required
instance offerings, service-linked roles, EKS/add-on/AMI availability, the
expected account, and a non-overlapping VPC CIDR.

```sh
unset RUNNER_PLATFORM_STACK
scripts/runner-platform.sh aws init
scripts/runner-platform.sh aws plan
scripts/runner-platform.sh aws show
scripts/runner-platform.sh aws apply
scripts/runner-platform.sh aws plan
test "$(terraform -chdir=terraform/aws/runner-fleet plan -detailed-exitcode >/dev/null; echo $?)" = 0
export KUBECONFIG="$PWD/.aws-runner.kubeconfig"
scripts/runner-platform.sh aws kubeconfig
export EKS_CLUSTER_NAME="$(terraform -chdir=terraform/aws/runner-fleet output -raw cluster_name)"
scripts/aws-addons.sh install
```

The second saved plan must report no changes. Recreate the existing GitHub App
and any GHCR pull secrets manually after the cluster exists; Terraform never
receives those values. ECR images use node-role pull access and need no image
pull secret.

Mirror and verify the immutable runner manifest before ARC deployment:

```sh
scripts/mirror-runner-image.sh copy \
  ghcr.io/f5-sales-demo/self-hosted-runner@sha256:<digest> \
  "$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/self-hosted-runner"
```

Use the same command with the `renovate` source and destination repositories to
populate the second ECR repository; the helper rejects cross-repository or
digest mismatches and compares the raw manifests byte for byte.

Then deploy ARC with the existing repository configs and run scale-from-zero,
scale-to-zero, socketless, compute, container-build, restart/OOM, CloudWatch,
and VPC-flow acceptance checks. Production remains serial and the candidate
scale set remains idle except during a monitored qualification campaign.

## Parallel qualification

Refreeze source only after the worker launcher and every package caller accept
the intended worker counts. Use one immutable runner image on `m6a.4xlarge`,
`--max-concurrency=2`, no `--concurrent`, and production
`XCSH_TEST_FILE_WORKERS=0`. Rotate five serial, five `--parallel=2`, and five
`--parallel=4` cold and warm samples. Promote only the lowest count with ten
valid matched comparisons, byte-identical output, zero failures/OOMs/evictions/
restarts, memory below 80 percent, no p95 regression, and at least 20 percent
lower median TypeScript duration. Test 6 and then 8 only if 2 and 4 fail, each
with fresh matched controls.
