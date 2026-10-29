#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export HUB_PORT="${1:-8800}"
if ! [[ "$HUB_PORT" =~ ^[0-9]+$ ]] || (( HUB_PORT < 1 || HUB_PORT > 65535 )); then
  echo '端口必须是 1–65535 的整数。' >&2
  exit 1
fi
docker compose -p bill-gen up -d --build
echo "bill-gen 已启动：http://127.0.0.1:$HUB_PORT"
