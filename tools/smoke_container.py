#!/usr/bin/env python3
"""Smoke an already-built Docker image using a unique, disposable container.

No build or pull, no fixed host port, and no existing service required.
Exit codes: 0 success, 1 Docker/runtime/contract failure, 2 CLI usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from http_smoke import SmokeError, positive_timeout, run_smoke, wait_ready

LABEL = "billgen.smoke.run"


def docker(*args, timeout=30):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise SmokeError(f"docker {' '.join(args)} exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def verified_remove(container_id: str, name: str, token: str):
    # Resolve and verify the exact immutable ID, name and per-run ownership label before deletion.
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise SmokeError(f"invalid container ID for cleanup: {container_id!r}")
    info = json.loads(docker("inspect", container_id))[0]
    if (info["Id"] != container_id or info["Name"] != "/" + name
            or info["Config"].get("Labels", {}).get(LABEL) != token):
        raise SmokeError("container cleanup identity/ownership mismatch")
    docker("rm", "--force", "--volumes", container_id)


def smoke(args):
    docker("info", "--format", "{{.ServerVersion}}", timeout=args.docker_timeout)
    docker("image", "inspect", args.image, timeout=args.docker_timeout)
    token = uuid.uuid4().hex
    name = "billgen-smoke-" + token
    with tempfile.TemporaryDirectory(prefix="billgen-container-smoke-") as temporary:
        cidfile = Path(temporary) / "container.cid"
        container_id = None
        try:
            docker("create", "--pull=never", "--name", name, "--cidfile", str(cidfile),
                   "--label", f"{LABEL}={token}", "--publish", "127.0.0.1::8800",
                   "--env", "BILL_GEN_DATA_DIR=/data", "--tmpfs", "/data:rw,nosuid,size=256m",
                   args.image, "billgen", "serve", "--host", "0.0.0.0", "--port", "8800",
                   timeout=args.docker_timeout)
            container_id = cidfile.read_text().strip()
            if not re.fullmatch(r"[a-f0-9]{64}", container_id):
                raise SmokeError("docker create returned invalid ID")
            docker("start", container_id, timeout=args.docker_timeout)
            info = json.loads(docker("inspect", container_id, timeout=args.docker_timeout))[0]
            bindings = info["NetworkSettings"]["Ports"]["8800/tcp"]
            binding = next(item for item in bindings if item["HostIp"] == "127.0.0.1")
            base_url = "http://127.0.0.1:" + str(int(binding["HostPort"]))
            wait_ready(base_url, startup_timeout=args.startup_timeout)
            result = run_smoke(base_url, timeout=args.request_timeout, template_id=args.template)
            print(json.dumps({"mode": "container", "image": args.image, **result}))
        except BaseException:
            if container_id:
                try:
                    print("--- container log ---\n" + docker("logs", "--tail", "150", container_id), file=sys.stderr)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    print(f"container logs unavailable: {exc}", file=sys.stderr)
            raise
        finally:
            # cidfile also records an ID if docker create completed just before a timeout.
            if cidfile.exists():
                verified_remove(cidfile.read_text().strip(), name, token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="already-built local Docker image")
    parser.add_argument("--template", default="boa-personal")
    parser.add_argument("--docker-timeout", type=positive_timeout, default=30)
    parser.add_argument("--startup-timeout", type=positive_timeout, default=45)
    parser.add_argument("--request-timeout", type=positive_timeout, default=90)
    args = parser.parse_args()
    try:
        smoke(args)
    except (OSError, ValueError, KeyError, StopIteration, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"container smoke FAILED: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
