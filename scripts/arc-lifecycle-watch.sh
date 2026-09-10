#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <pods|nodes> <output.jsonl>" >&2
  exit 2
}

[[ $# -eq 2 ]] || usage
kind=$1
output=$2
case "$kind" in pods | nodes) ;; *) usage ;; esac

command -v kubectl >/dev/null
command -v jq >/dev/null
mkdir -p "$(dirname "$output")"
touch "$output"

while true; do
  if [[ "$kind" == pods ]]; then
    if kubectl get pods -A -l actions.github.com/scale-set-name \
      --watch --output-watch-events -o json |
      jq --unbuffered -c '{
        observed_at: (now | todateiso8601),
        event_type: .type,
        pod: {
          namespace: .object.metadata.namespace,
          name: .object.metadata.name,
          created_at: .object.metadata.creationTimestamp,
          deleted_at: .object.metadata.deletionTimestamp,
          scale_set: .object.metadata.labels["actions.github.com/scale-set-name"],
          node: .object.spec.nodeName,
          phase: .object.status.phase,
          reason: .object.status.reason,
          message: .object.status.message,
          started_at: .object.status.startTime,
          conditions: .object.status.conditions,
          container_statuses: .object.status.containerStatuses
        }
      }' >>"$output"; then
      status=0
    else
      status=$?
    fi
  elif kubectl get nodes --watch --output-watch-events -o json |
    jq --unbuffered -c '{
      observed_at: (now | todateiso8601),
      event_type: .type,
      node: {
        name: .object.metadata.name,
        created_at: .object.metadata.creationTimestamp,
        profile: .object.metadata.labels["runner-profile"],
        unschedulable: (.object.spec.unschedulable // false),
        allocatable: {
          cpu: .object.status.allocatable.cpu,
          memory: .object.status.allocatable.memory
        },
        conditions: .object.status.conditions
      }
    }' >>"$output"; then
    status=0
  else
    status=$?
  fi
  printf '%s %s watch ended with status %s; reconnecting\n' \
    "$(date -u +%FT%TZ)" "$kind" "$status" >&2
  sleep 2
done
