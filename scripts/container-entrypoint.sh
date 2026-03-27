#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=/app
mkdir -p "${RNS_CONFIG_DIR}" "${LXMD_CONFIG_DIR}" "${APP_DATA_DIR}"

python /app/scripts/render_configs.py

rnsd --config "${RNS_CONFIG_DIR}" --service &
RNSD_PID=$!

lxmd --config "${LXMD_CONFIG_DIR}" --rnsconfig "${RNS_CONFIG_DIR}" --service -p &
LXMD_PID=$!

sleep 3

uvicorn app.main:app --host 0.0.0.0 --port "${WEB_PORT}" &
WEB_PID=$!

shutdown() {
  kill "${WEB_PID}" "${LXMD_PID}" "${RNSD_PID}" 2>/dev/null || true
  wait "${WEB_PID}" "${LXMD_PID}" "${RNSD_PID}" 2>/dev/null || true
}

trap shutdown SIGTERM SIGINT
wait -n "${WEB_PID}" "${LXMD_PID}" "${RNSD_PID}"
shutdown
