#!/usr/bin/env bash
set -euo pipefail

readonly minimum_abi=2
readonly probe=${1:-/usr/local/bin/landlock-abi}
readonly remediation="use Ubuntu HWE or another newer kernel/host exposing Landlock ABI 2 or newer"

if ! observed=$("$probe" 2>/dev/null); then
  printf 'runner admission denied: Landlock is unavailable or blocked; %s\n' \
    "$remediation" >&2
  exit 78
fi

if [[ ! "$observed" =~ ^[0-9]+$ ]]; then
  printf 'runner admission denied: invalid ABI result; %s\n' "$remediation" >&2
  exit 78
fi

if ((observed < minimum_abi)); then
  printf 'runner admission denied: observed ABI %d; %s\n' \
    "$observed" "$remediation" >&2
  exit 78
fi

printf 'runner admission accepted: Landlock ABI %d\n' "$observed"
