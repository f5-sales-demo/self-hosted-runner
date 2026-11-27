#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 <derived-renovate-image>" >&2; exit 2; }
image=$1
version_pattern='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source_receipt="$repo_root/renovate-system/image-source.json"
expected_version=$(jq -er --arg pattern "$version_pattern" '.upstream.version | select(type == "string") | select(test($pattern))' "$source_receipt")
node_version=$(jq -er --arg pattern "$version_pattern" '.runtime.node | select(type == "string") | select(test($pattern))' "$source_receipt")
if [[ -n "${RENOVATE_RUNTIME_EVIDENCE_DIR:-}" ]]; then
  evidence=$RENOVATE_RUNTIME_EVIDENCE_DIR
  mkdir -m 0700 -- "$evidence"
else
  evidence=$(mktemp -d)
  trap 'rm -rf -- "$evidence"' EXIT
fi
chmod 0777 "$evidence"
printf '%s' 'ghs_structured.token-0123456789' >"$evidence/installation-token"
chmod 0666 "$evidence/installation-token"

output=$(docker run --rm --read-only --cidfile "$evidence/version-container-id" \
  --volume "$evidence:/token" \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --tmpfs /work:rw,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --tmpfs /cache:rw,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --tmpfs /opt/containerbase:rw,exec,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --env RENOVATE_TOKEN_FILE=/token/installation-token \
  "$image" --version 2>&1)
grep -Fxq "$expected_version" <<<"$output"
[[ ! -e "$evidence/installation-token" ]]
if grep -Fq 'Initializing tools all' <<<"$output"; then
  echo "Renovate unexpectedly initialized the hidden upstream tool cache" >&2
  exit 1
fi

tool_output=$(docker run --rm --read-only --user 12021:0 --cidfile "$evidence/tool-container-id" \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --tmpfs /opt/containerbase:rw,exec,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --entrypoint sh \
  "$image" -lc '
    cp -a /opt/f5-renovate/containerbase/. /tmp/containerbase/
    cp -a /opt/f5-renovate/containerbase-runtime/. /opt/containerbase/
    install-tool node "$1"
  ' sh "$node_version" 2>&1)
grep -Fq 'Install tool node succeeded' <<<"$tool_output"
if grep -Eq 'ERROR|FATAL' <<<"$tool_output"; then
  echo "Renovate emitted an error while provisioning Node" >&2
  exit 1
fi
printf 'verified read-only non-root Renovate runtime, token deletion, and Node provisioning\n'
