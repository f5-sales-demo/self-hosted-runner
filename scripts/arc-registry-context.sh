#!/usr/bin/env bash
set -euo pipefail
umask 077

[[ $# -eq 1 ]] || { echo "usage: $0 <temporary Helm registry configuration>" >&2; exit 2; }
destination=$1
source_docker_config=${GHCR_SOURCE_DOCKER_CONFIG:-}
if [[ -n "$source_docker_config" ]]; then
  source_config="$source_docker_config/config.json"
  [[ -r "$source_config" && ! "$destination" -ef "$source_config" ]] || {
    echo "configured GHCR source credentials are unreadable or destination is not isolated" >&2
    exit 1
  }
  if ! jq -se '
    if length != 1 then error("expected one source configuration document")
    else .[0] end |
    . as $input |
    ($input.auths["ghcr.io"] // null) as $auth |
    ($input.credHelpers["ghcr.io"] // $input.credsStore // null) as $helper |
    if ($helper | type == "string" and length > 0) then
      {auths: {}, credHelpers: {"ghcr.io": $helper}}
    elif ($auth | type == "object") and
      ([$auth.auth, $auth.identitytoken] |
        any(.[]; type == "string" and length > 0)) then
      {auths: {"ghcr.io": $auth}}
    else error("configured GHCR credentials are unavailable") end
  ' "$source_config" >"$destination"; then
    echo "configured GHCR source credentials are invalid" >&2
    exit 1
  fi
else
  printf '{}\n' >"$destination"
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    gh auth token | HELM_REGISTRY_CONFIG="$destination" helm registry login ghcr.io \
      --username "$(gh api user --jq .login)" --password-stdin >/dev/null
  fi
fi
chmod 0600 "$destination"
