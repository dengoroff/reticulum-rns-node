#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=/app
mkdir -p "${RNS_CONFIG_DIR}" "${LXMD_CONFIG_DIR}" "${APP_DATA_DIR}"

echo "[entrypoint] Rendering config files"
python /app/scripts/render_configs.py

echo "[entrypoint] Starting rnsd with config dir ${RNS_CONFIG_DIR}"
rnsd --config "${RNS_CONFIG_DIR}" --service &
RNSD_PID=$!

echo "[entrypoint] Starting lxmd with config dir ${LXMD_CONFIG_DIR}"
lxmd --config "${LXMD_CONFIG_DIR}" --rnsconfig "${RNS_CONFIG_DIR}" --service -p &
LXMD_PID=$!

sleep 3

echo "[entrypoint] Starting web UI on port ${WEB_PORT}"
uvicorn app.main:app --host 0.0.0.0 --port "${WEB_PORT}" &
WEB_PID=$!

shutdown() {
  kill "${WEB_PID}" "${LXMD_PID}" "${RNSD_PID}" 2>/dev/null || true
  wait "${WEB_PID}" "${LXMD_PID}" "${RNSD_PID}" 2>/dev/null || true
}

trap shutdown SIGTERM SIGINT
wait -n "${WEB_PID}" "${LXMD_PID}" "${RNSD_PID}"
shutdown
