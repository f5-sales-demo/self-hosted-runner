#!/usr/bin/env bash
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG must point to the protected AKS administrator config}"
: "${GITHUB_APP_ID:?GITHUB_APP_ID is required}"
: "${GITHUB_APP_INSTALLATION_ID:?GITHUB_APP_INSTALLATION_ID is required}"
: "${GITHUB_APP_PRIVATE_KEY_FILE:?GITHUB_APP_PRIVATE_KEY_FILE is required}"

[[ "$GITHUB_APP_ID" =~ ^[0-9]+$ ]]
[[ "$GITHUB_APP_INSTALLATION_ID" =~ ^[0-9]+$ ]]
[[ -f "$GITHUB_APP_PRIVATE_KEY_FILE" ]]
case "$(stat -c '%a' "$GITHUB_APP_PRIVATE_KEY_FILE")" in
  400|600) ;;
  *) echo "GitHub App private key must have mode 0400 or 0600" >&2; exit 1 ;;
esac

for namespace in arc-runners-socketless arc-runners-container-build; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  manifest=$(mktemp)
  trap 'rm -f "$manifest"' EXIT
  chmod 0600 "$manifest"
  kubectl -n "$namespace" create secret generic arc-github-app     --from-literal=github_app_id="$GITHUB_APP_ID"     --from-literal=github_app_installation_id="$GITHUB_APP_INSTALLATION_ID"     --from-file=github_app_private_key="$GITHUB_APP_PRIVATE_KEY_FILE"     --dry-run=client -o yaml >"$manifest"
  kubectl apply -f "$manifest" >/dev/null
  rm -f "$manifest"
  trap - EXIT
  printf 'updated secret in %s\n' "$namespace"
done
