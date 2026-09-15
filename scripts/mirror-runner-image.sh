#!/usr/bin/env bash
set -euo pipefail

mode=${1:-}
source_image=${2:-}
target=${3:-}
usage="usage: $0 <copy|verify> <immutable source digest> <target repository|immutable target digest>"
[[ "$mode" == copy || "$mode" == verify ]] || { echo "$usage" >&2; exit 2; }
[[ -n "$source_image" && -n "$target" ]] || { echo "$usage" >&2; exit 2; }
reference_pattern='^(ghcr\.io/f5-sales-demo|f5salesdemoarcca\.azurecr\.io|[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com)/(self-hosted-runner|renovate)@(sha256:[0-9a-f]{64})$'
[[ "$source_image" =~ $reference_pattern ]] || { echo "source must be an approved immutable GHCR, ACR, or ECR digest" >&2; exit 2; }
repository=${BASH_REMATCH[2]}
digest=${BASH_REMATCH[3]}

for command in docker jq; do command -v "$command" >/dev/null; done
config=$(mktemp -d)
trap 'rm -rf -- "$config"' EXIT
chmod 0700 "$config"
export DOCKER_CONFIG="$config"

login_registry() {
  local registry=$1
  case "$registry" in
    ghcr.io)
      command -v gh >/dev/null
      gh auth token | docker login ghcr.io --username "$(gh api user --jq .login)" --password-stdin >/dev/null
      ;;
    f5salesdemoarcca.azurecr.io)
      command -v az >/dev/null
      : "${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID must identify the target subscription}"
      [[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
      [[ "$(az acr show --name f5salesdemoarcca --query location -o tsv)" == canadacentral ]]
      az acr login --name f5salesdemoarcca --expose-token --output json >"$config/acr-token.json"
      jq -e . "$config/acr-token.json" >/dev/null
      jq -r .accessToken "$config/acr-token.json" | docker login "$registry" --username 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null
      ;;
    *.dkr.ecr.us-east-1.amazonaws.com)
      command -v aws >/dev/null
      : "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID must identify the target account}"
      [[ "$registry" == "$AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com" ]]
      [[ "$(aws sts get-caller-identity --query Account --output text)" == "$AWS_ACCOUNT_ID" ]]
      aws ecr describe-repositories --region us-east-1 --repository-names "$repository" >/dev/null
      aws ecr get-login-password --region us-east-1 | docker login "$registry" --username AWS --password-stdin >/dev/null
      ;;
    *) echo "unsupported registry: $registry" >&2; exit 2;;
  esac
}

login_registry "${source_image%%/*}"
if [[ "$mode" == copy ]]; then
  target_pattern='^(f5salesdemoarcca\.azurecr\.io|[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com)/(self-hosted-runner|renovate)$'
  [[ "$target" =~ $target_pattern && "${BASH_REMATCH[2]}" == "$repository" ]] || { echo "copy target must be the matching approved ACR or ECR repository" >&2; exit 2; }
  login_registry "${target%%/*}"
  tag="$target:approved-${digest:7:16}"
  docker buildx imagetools create --tag "$tag" "$source_image" >/dev/null
  mirror_image="$target@$digest"
else
  [[ "$target" =~ $reference_pattern ]] || { echo "verify target must be an approved immutable digest" >&2; exit 2; }
  [[ "${BASH_REMATCH[2]}" == "$repository" && "${BASH_REMATCH[3]}" == "$digest" ]] || { echo "source and target repository or digest differs" >&2; exit 1; }
  mirror_image=$target
  login_registry "${mirror_image%%/*}"
fi

source_raw=$(docker buildx imagetools inspect --raw "$source_image" | sha256sum | cut -d' ' -f1)
mirror_raw=$(docker buildx imagetools inspect --raw "$mirror_image" | sha256sum | cut -d' ' -f1)
[[ "sha256:$source_raw" == "$digest" && "$source_raw" == "$mirror_raw" ]] || {
  echo "source and mirror manifests are not byte-identical" >&2
  exit 1
}
printf '%s\n' "$mirror_image"
