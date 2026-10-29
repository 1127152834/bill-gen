#!/usr/bin/env bash
# One container, two processes: the Python renderer/API and the Node LangGraph service.
#
# They keep their own language runtimes and talk over MCP on stdio (the Node side spawns
# `billgen mcp`). If either dies the container dies too — a half-dead app that still answers
# health checks is worse than a restart.
set -euo pipefail

BILLGEN_PORT="${BILLGEN_PORT:-8800}"

log() { printf '[entrypoint] %s\n' "$*"; }

pids=()
stopping=0

shutdown() {
  stopping=1
  log "shutting down"
  for pid in "${pids[@]:-}"; do
    [ -n "${pid:-}" ] && kill -TERM "$pid" 2>/dev/null || true
  done
}

trap shutdown TERM INT

mkdir -p "${BILL_GEN_DATA_DIR:-/data}/billgen" "${BILLGEN_OUT:-/data/renders}" "$(dirname "${DATABASE_PATH:-/data/flow/app.db}")"

# 0.0.0.0 inside the container: the published mapping stays on 127.0.0.1 outside, but docker's
# proxy cannot reach a socket bound to the container loopback.
BILLGEN_HOST="${BILLGEN_HOST:-0.0.0.0}"
log "billgen serve on ${BILLGEN_HOST}:${BILLGEN_PORT} (data ${BILL_GEN_DATA_DIR:-/data})"
billgen serve --host "$BILLGEN_HOST" --port "$BILLGEN_PORT" &
pids+=("$!")

log "flow server on ${HOST:-127.0.0.1}:${PORT:-8797} (web ${WEB_DIST:-none}, mcp ${BILLGEN_MCP_CMD:-unset})"
cd /app/flow
./node_modules/.bin/tsx apps/server/src/index.ts &
pids+=("$!")

# Wait for the first process to exit, then take the other down with us.
set +e
wait -n
status=$?
set -e
log "a service exited with ${status}; stopping the rest"
shutdown
wait || true
exit "$status"
