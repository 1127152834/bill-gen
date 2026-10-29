#!/usr/bin/env bash
# 单一入口：billgen 既是渲染服务，也是账单猎手（Node/LangGraph）的监管者。
#
# 猎手由 billgen 拉起在容器内 127.0.0.1:8797，并经由 /hunter/ 与 /api/generations 同源转发，
# 因此对外只有一个端口、一份会话、一个入口页。模型配置从前端写入数据卷，保存后自动重启子进程。
set -euo pipefail

BILLGEN_PORT="${BILLGEN_PORT:-8800}"
BILLGEN_HOST="${BILLGEN_HOST:-0.0.0.0}"
BILLGEN_FLOW_ROOT="${BILLGEN_FLOW_ROOT:-/app/flow}"
BILLGEN_HUNTER_PORT="${BILLGEN_HUNTER_PORT:-8797}"

log() { printf '[entrypoint] %s\n' "$*"; }

mkdir -p "${BILL_GEN_DATA_DIR:-/data}/billgen" \
         "${BILLGEN_OUT:-/data/renders}" \
         "$(dirname "${BILLGEN_HUNTER_DB:-/data/flow/app.db}")"

export BILLGEN_FLOW_ROOT BILLGEN_HUNTER_PORT
export DATABASE_PATH="${BILLGEN_HUNTER_DB:-/data/flow/app.db}"
export BILLGEN_OUT="${BILLGEN_OUT:-/data/renders}"

log "billgen serve on ${BILLGEN_HOST}:${BILLGEN_PORT} (hunter ${BILLGEN_FLOW_ROOT} -> 127.0.0.1:${BILLGEN_HUNTER_PORT})"
exec billgen serve --host "$BILLGEN_HOST" --port "$BILLGEN_PORT" \
     --hunter-port "$BILLGEN_HUNTER_PORT"
