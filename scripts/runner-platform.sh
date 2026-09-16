#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 ]] || { echo "usage: $0 <azure|aws> <init|plan|show|apply|destroy-plan|kubeconfig>" >&2; exit 2; }
cloud=$1
action=$2
stack=${RUNNER_PLATFORM_STACK:-runner-fleet}
case "$cloud" in azure|aws) ;; *) echo "cloud must be azure or aws" >&2; exit 2;; esac
case "$action" in init|plan|show|apply|destroy-plan|kubeconfig) ;; *) echo "unsupported action: $action" >&2; exit 2;; esac
case "$stack" in bootstrap|runner-fleet) ;; *) echo "RUNNER_PLATFORM_STACK must be bootstrap or runner-fleet" >&2; exit 2;; esac
[[ "$action" != kubeconfig || "$stack" == runner-fleet ]] || { echo "kubeconfig is available only for runner-fleet" >&2; exit 2; }

repo_root=$(git rev-parse --show-toplevel)
root="$repo_root/terraform/$cloud/$stack"
backend="$root/backend.hcl"
plan_dir="$repo_root/.plans"
plan="$plan_dir/$cloud-$stack.tfplan"
[[ -d "$root" ]] || { echo "Terraform root does not exist: $root" >&2; exit 2; }

backend_declaration="$root/backend.tf"
disabled_backend_declaration="$root/.backend.tf.runner-platform-local"
restore_backend_declaration() {
  if [[ -f "$disabled_backend_declaration" ]]; then
    mv -- "$disabled_backend_declaration" "$backend_declaration"
  fi
}
if [[ -e "$disabled_backend_declaration" ]]; then
  [[ ! -e "$backend_declaration" ]] || { echo "both active and disabled backend declarations exist" >&2; exit 1; }
  restore_backend_declaration
fi
if [[ "$stack" == bootstrap && ! -f "$backend" ]]; then
  [[ ${MIGRATE_STATE:-no} != yes ]] || { echo "create ignored backend.hcl before migrating bootstrap state" >&2; exit 1; }
  [[ -f "$backend_declaration" ]] || { echo "bootstrap backend declaration is missing" >&2; exit 1; }
  mv -- "$backend_declaration" "$disabled_backend_declaration"
  trap restore_backend_declaration EXIT
fi

check_identity() {
  if [[ "$cloud" == aws ]]; then
    : "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID must identify the target account}"
    export AWS_REGION=${AWS_REGION:-us-east-1}
    export TF_VAR_aws_account_id=$AWS_ACCOUNT_ID TF_VAR_aws_region=$AWS_REGION
    caller=$(aws sts get-caller-identity --query Account --output text)
    [[ "$caller" == "$AWS_ACCOUNT_ID" ]] || { echo "AWS account mismatch: $caller" >&2; exit 1; }
    [[ "$AWS_REGION" == us-east-1 ]] || { echo "AWS_REGION must be us-east-1" >&2; exit 1; }
  else
    : "${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID must identify the target subscription}"
    [[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]] || { echo "Azure subscription mismatch" >&2; exit 1; }
  fi
}

init_backend() {
  if [[ "$stack" == bootstrap && ! -f "$backend" ]]; then
    umask 077
    terraform -chdir="$root" init -reconfigure
    return
  fi
  [[ -f "$backend" ]] || { echo "copy backend.hcl.example to ignored backend.hcl and replace identifiers" >&2; exit 1; }
  if [[ "$stack" == bootstrap && ${MIGRATE_STATE:-no} == yes ]]; then
    terraform -chdir="$root" init -migrate-state -backend-config=backend.hcl
  else
    terraform -chdir="$root" init -reconfigure -backend-config=backend.hcl
  fi
}

check_identity
case "$action" in
  init)
    init_backend
    ;;
  plan)
    init_backend
    [[ "$cloud" != aws || "$stack" != runner-fleet ]] || "$repo_root/scripts/aws-preflight.sh"
    mkdir -p "$plan_dir"
    terraform -chdir="$root" plan -out="$plan"
    if [[ "$cloud" == aws ]]; then
      terraform show -json "$plan" >"$plan.json"
      jq -e . "$plan.json" >/dev/null
      "$repo_root/scripts/aws-plan-preflight.py" "$plan.json"
    fi
    printf 'saved plan: %s\n' "$plan"
    ;;
  show)
    [[ -f "$plan" ]] || { echo "saved plan does not exist: $plan" >&2; exit 1; }
    terraform -chdir="$root" show "$plan"
    ;;
  apply)
    [[ -f "$plan" ]] || { echo "saved plan does not exist: $plan" >&2; exit 1; }
    if [[ "$cloud" == aws ]]; then
      terraform show -json "$plan" >"$plan.json"
      jq -e . "$plan.json" >/dev/null
      "$repo_root/scripts/aws-plan-preflight.py" "$plan.json"
    fi
    terraform -chdir="$root" apply "$plan"
    if [[ "$stack" == bootstrap && -f "$root/terraform.tfstate" ]]; then
      chmod 0600 "$root/terraform.tfstate"
    fi
    ;;
  destroy-plan)
    : "${ALLOW_DESTROY_PLAN:?set ALLOW_DESTROY_PLAN to the exact value yes}"
    [[ "$ALLOW_DESTROY_PLAN" == yes ]] || { echo "ALLOW_DESTROY_PLAN must equal yes" >&2; exit 1; }
    init_backend
    mkdir -p "$plan_dir"
    terraform -chdir="$root" plan -destroy -out="$plan"
    if [[ "$cloud" == aws ]]; then
      terraform show -json "$plan" >"$plan.json"
      jq -e . "$plan.json" >/dev/null
      "$repo_root/scripts/aws-plan-preflight.py" --allow-destroy "$plan.json"
    fi
    printf 'saved destroy plan: %s\n' "$plan"
    ;;
  kubeconfig)
    : "${KUBECONFIG:?KUBECONFIG must name an ignored kubeconfig path}"
    init_backend
    cluster=$(terraform -chdir="$root" output -raw cluster_name)
    if [[ "$cloud" == aws ]]; then
      aws eks update-kubeconfig --region "$AWS_REGION" --name "$cluster" --kubeconfig "$KUBECONFIG" --alias "$cluster"
    else
      group=$(terraform -chdir="$root" output -raw resource_group_name)
      az aks get-credentials --resource-group "$group" --name "$cluster" --file "$KUBECONFIG" --overwrite-existing
    fi
    chmod 0600 "$KUBECONFIG"
    ;;
esac
