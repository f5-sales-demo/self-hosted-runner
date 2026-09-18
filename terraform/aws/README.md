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
- The normal enabled capacity is 492 vCPUs including system nodes and requires
  a 615-vCPU standard On-Demand quota. Setting
  `enable_compute_32_vcpu_candidate=true` adds exactly one scale-to-zero
  `c6a.8xlarge` node group, raising maximum capacity to 524 vCPUs and the
  required quota floor to 655 vCPUs. The shared pool remains disabled and its
  production maxima are unchanged.
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
also requires 615 standard On-Demand vCPUs when the candidate is off, or 655
when it is on, three unused EIP slots, all required instance offerings
(including `c6a.8xlarge` in all three AZs when enabled), service-linked roles,
EKS/add-on/AMI availability, the expected account, and a non-overlapping VPC
CIDR.

```sh
unset RUNNER_PLATFORM_STACK
scripts/runner-platform.sh aws init
export TF_VAR_enable_compute_32_vcpu_candidate=true
scripts/runner-platform.sh aws plan
scripts/runner-platform.sh aws show
scripts/aws-preflight.sh .plans/aws-runner-fleet.tfplan
scripts/runner-platform.sh aws apply
scripts/runner-platform.sh aws plan
test "$(terraform -chdir=terraform/aws/runner-fleet plan -detailed-exitcode >/dev/null; echo $?)" = 0
export KUBECONFIG="$PWD/.aws-runner.kubeconfig"
scripts/runner-platform.sh aws kubeconfig
export EKS_CLUSTER_NAME="$(terraform -chdir=terraform/aws/runner-fleet output -raw cluster_name)"
scripts/aws-addons.sh install
```

Keep `TF_VAR_enable_compute_32_vcpu_candidate=true` set while creating the xcsh
GitHub App/GHCR secrets and running
`scripts/arc-deploy.sh arc/repositories/xcsh.yaml`; the ARC helpers then include
the otherwise-disabled candidate only for xcsh.

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

## One-runner `parallel=20` qualification

Keep both the node group and `xcsh-compute-32-vcpu-density-candidate` ARC scale
set at zero when idle. After the 660-vCPU Standard On-Demand quota request is
approved, review the saved AWS-only plan: only the candidate launch template,
node group, and autoscaler discovery tags may change, and there must be no Azure
provider or resource action. Apply that saved plan, deploy the candidate scale
set, and confirm one runner scales from zero onto a `c6a.8xlarge`, the node has
more than 30 allocatable CPUs, the pod is Guaranteed at 30 CPU/56 GiB, and both
ARC and the node group return to zero after the job.

Freeze one xcsh source SHA and one immutable ECR image digest. Dispatch exactly
one serial control with `file_workers=0` and one `f32-parallel` candidate with
`file_workers=20`. Promote production from its current value of 10 to
20 only when manifests and output inventory are identical, failures, OOMs,
evictions, and restarts are all zero, there is no memory or node pressure, and
both TypeScript and end-to-end critical-path durations are lower. Otherwise keep
production at 10 and roll the candidate back to 0. Record source/image identity,
run IDs, pod resources, approved quota, metrics, decision, and rollback in the
linked issue.
