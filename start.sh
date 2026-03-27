#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

export WEB_PORT="${WEB_PORT:-8080}"
export RNS_SERVER_PORT="${RNS_SERVER_PORT:-4242}"

docker compose build
docker compose up -d

echo "reticulum-node is starting"
echo "Web UI: http://localhost:${WEB_PORT}"
echo "Reticulum TCP port: ${RNS_SERVER_PORT}"
