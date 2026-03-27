#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

export WEB_PORT="${WEB_PORT:-8080}"
export RNS_SERVER_PORT="${RNS_SERVER_PORT:-4242}"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Docker Compose is not available. Install 'docker compose' plugin or 'docker-compose'." >&2
  exit 1
fi

"${COMPOSE_CMD[@]}" build
"${COMPOSE_CMD[@]}" up -d

echo "reticulum-node is starting"
echo "Web UI: http://localhost:${WEB_PORT}"
echo "Reticulum TCP port: ${RNS_SERVER_PORT}"
