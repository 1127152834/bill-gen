#!/usr/bin/env bash
# 单一入口：billgen 既是渲染引擎也是 Web 工作台与统一 API，对外只有一个端口。
set -euo pipefail

BILLGEN_PORT="${BILLGEN_PORT:-8800}"
BILLGEN_HOST="${BILLGEN_HOST:-0.0.0.0}"

log() { printf '[entrypoint] %s\n' "$*"; }

mkdir -p "${BILL_GEN_DATA_DIR:-/data}/billgen" \
         "${BILLGEN_OUT:-/data/renders}"

export BILLGEN_OUT="${BILLGEN_OUT:-/data/renders}"

log "billgen serve on ${BILLGEN_HOST}:${BILLGEN_PORT}"
exec billgen serve --host "$BILLGEN_HOST" --port "$BILLGEN_PORT"
