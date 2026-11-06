#!/usr/bin/env bash
set -euo pipefail

# Safe, explicit preflight for the local Ubuntu VM.  A stale ai-trader-pre
# container can share the production workspace/database and must never be
# silently left running.  This script only reports; pass --remove-stale after
# reviewing the listed containers to stop and remove them.

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

mapfile -t stale < <(docker ps -a --format '{{.Names}}' | grep -E '^ai-trader-pre-' || true)
if ((${#stale[@]})); then
  printf 'stale pre-production containers found:\n'
  printf '  %s\n' "${stale[@]}"
  if [[ "${1:-}" != "--remove-stale" ]]; then
    echo 'Refusing to continue. Re-run with --remove-stale after review.' >&2
    exit 2
  fi
  for name in "${stale[@]}"; do
    docker update --restart=no "$name" >/dev/null
    docker stop --time 20 "$name" >/dev/null || true
    docker rm "$name" >/dev/null
  done
else
  echo 'no stale ai-trader-pre containers found'
fi

docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' \
  --filter name=^ai-trader$ --filter name=^ai-ngrok$

echo 'preflight complete; validate /health and the public webhook before enabling trading'
