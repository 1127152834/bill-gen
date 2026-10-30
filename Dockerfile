# One image, one runtime: the Python renderer/API (billgen) and its web workbench.
FROM python:3.11-bookworm-slim
ENV PATH="/opt/billgen/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BILL_GEN_DATA_DIR=/data/billgen \
    BILLGEN_OUT=/data/renders
# Mirrors flake; retry, and drop a suite that 503s instead of failing the whole build.
RUN set -eux; \
    for attempt in 1 2 3 4 5; do \
      if apt-get update -o Acquire::Retries=3; then break; fi; \
      sed -i '/bookworm-updates/d' /etc/apt/sources.list; sleep 3; \
    done; \
    apt-get install -y --no-install-recommends \
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

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data/billgen /data/renders

WORKDIR /app
VOLUME ["/data"]
EXPOSE 8800
HEALTHCHECK --interval=15s --timeout=6s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8800/healthz >/dev/null
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
