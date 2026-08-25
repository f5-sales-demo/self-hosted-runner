#!/usr/bin/env bash
set -euo pipefail

mapfile -t forbidden < <(
  git ls-files |
    grep -E '(^|/)(backend\.hcl|kubeconfig[^/]*|[^/]+\.tfvars(\.json)?|[^/]+\.tfstate(\..*)?|[^/]+\.tfplan|[^/]+\.pem|[^/]+\.key|[^/]+\.local\.ya?ml)$' ||
    true
)

if (( ${#forbidden[@]} > 0 )); then
  echo "Deployment-local or secret-bearing artifacts must not be committed:" >&2
  printf '%s\n' "${forbidden[@]}" >&2
  exit 1
fi

report=$(mktemp)
trap 'rm -f "$report"' EXIT
if git grep -Il '' -- . ':!tests/fixtures/**' |
  xargs -r grep -lE -- '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----' >"$report"; then
  echo "A committed file appears to contain a private key:" >&2
  sed -n '1,40p' "$report" >&2
  exit 1
fi
