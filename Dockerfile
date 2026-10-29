# One image, both runtimes: the Python renderer/API (billgen) and the Node LangGraph service (flow).
#
# Stage 1 builds the web UI (Vite needs dev dependencies, the runtime does not need a build tool).
# Stage 2 keeps Node (flow runs TypeScript through tsx) and installs billgen into its own venv, so the
# two halves never share an interpreter or a package manager.
FROM node:22-bookworm-slim AS flow-build
RUN corepack enable
WORKDIR /app/flow
# Copy only what the install needs, so editing a source file does not re-download the store.
COPY flow/package.json flow/pnpm-lock.yaml flow/pnpm-workspace.yaml flow/tsconfig.base.json flow/tsconfig.json ./
COPY flow/apps/server/package.json apps/server/
COPY flow/apps/web/package.json apps/web/
COPY flow/packages/core/package.json packages/core/
RUN pnpm install --frozen-lockfile
COPY flow/ ./
RUN pnpm --filter @bh/web build

FROM node:22-bookworm-slim AS runtime
ENV PATH="/opt/billgen/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BILL_GEN_DATA_DIR=/data/billgen \
    BILLGEN_OUT=/data/renders \
    BILLGEN_MCP_CMD=/opt/billgen/bin/billgen \
    BILLGEN_TEMPLATES=auto \
    BILLGEN_AUTO_LIMIT=2 \
    BILLGEN_AI_FILL=1 \
    DATABASE_PATH=/data/flow/app.db \
    WEB_DIST=apps/web/dist \
    HOST=0.0.0.0 \
    PORT=8797
# Mirrors flake; retry, and drop a suite that 503s instead of failing the whole build.
RUN set -eux; \
    for attempt in 1 2 3 4 5; do \
      if apt-get update -o Acquire::Retries=3; then break; fi; \
      sed -i '/bookworm-updates/d' /etc/apt/sources.list; sleep 3; \
    done; \
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        fonts-liberation2 fonts-noto-cjk curl ca-certificates; \
    rm -rf /var/lib/apt/lists/*

# billgen (Python): hydrated assets are already real files in the build context.
WORKDIR /app/billgen
COPY pyproject.toml constraints-ci.txt README.md runtime_paths.py ./
COPY src/ src/
COPY scripts/ scripts/
COPY examples/ examples/
RUN python3 -m venv /opt/billgen \
    && /opt/billgen/bin/pip install --no-cache-dir -c constraints-ci.txt '.[mcp]' \
    && /opt/billgen/bin/python scripts/assets.py check

# flow (Node): the built workspace, including the compiled web UI.
WORKDIR /app/flow
COPY --from=flow-build /app/flow ./

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data/billgen /data/renders /data/flow

WORKDIR /app
VOLUME ["/data"]
EXPOSE 8797 8800
HEALTHCHECK --interval=15s --timeout=6s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8800/healthz >/dev/null && curl -fsS http://127.0.0.1:8797/api/health >/dev/null
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
