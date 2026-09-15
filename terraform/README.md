# Portable runner platform

Azure and AWS are independent Terraform stacks. They have different backends,
provider roots, and state keys; selecting a cloud never initializes or plans
the other cloud. The shared `runner-pools.json` file is the cloud-neutral
capacity and scheduling contract.

Use `scripts/runner-platform.sh <azure|aws> <action>`. The default stack is
`runner-fleet`; set `RUNNER_PLATFORM_STACK=bootstrap` only for backend
bootstrap operations. Deployment inputs, backend files, plans, state, and
kubeconfigs are local ignored artifacts.

See `azure/README.md` and `aws/README.md` for provider-specific bootstrap,
preflight, saved-plan, and deployment procedures.
