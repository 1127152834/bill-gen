#!/usr/bin/env python3
"""Install a wheel offline into a fresh venv, then smoke it outside the checkout.

Prepare dependencies explicitly before running, e.g. pip download -d wheelhouse .
Exit codes: 0 success, 1 installation/runtime/contract failure, 2 CLI usage error.
No dependency downloads, retries, editable install, PYTHONPATH, or system-site-packages.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from http_smoke import SmokeError, positive_timeout, run_smoke, wait_ready


def clean_env():
    # Inherited PIP_TARGET/PIP_PREFIX/PIP_USER could silently escape the isolated venv.
    env = {name: value for name, value in os.environ.items() if not name.startswith("PIP_")}
    for name in ("PYTHONPATH", "PYTHONHOME", "BILLGEN_ROOT", "BILL_GEN_DATA_DIR", "VIRTUAL_ENV"):
        env.pop(name, None)
    env.update(PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1", PIP_CONFIG_FILE=os.devnull,
               PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PIP_NO_INDEX="1")
    return env


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def smoke(args):
    wheel = args.wheel.resolve(strict=True)
    if wheel.suffix != ".whl" or not wheel.is_file():
        raise SmokeError(f"expected wheel file: {wheel}")
    wheelhouse = (args.wheelhouse or wheel.parent).resolve(strict=True)
    if not wheelhouse.is_dir():
        raise SmokeError(f"expected wheelhouse directory: {wheelhouse}")
    with tempfile.TemporaryDirectory(prefix="billgen-install-smoke-") as temporary:
        root = Path(temporary)
        outside = root / "outside"
        outside.mkdir()
        venv = root / "venv"
        env = clean_env()
        subprocess.run([sys.executable, "-I", "-m", "venv", str(venv)], cwd=outside,
                       env=env, check=True, timeout=args.install_timeout)
        bindir = venv / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        entrypoint = bindir / ("billgen.exe" if os.name == "nt" else "billgen")
        subprocess.run([str(python), "-I", "-m", "pip", "install", "--no-index", "--retries", "0",
                        "--timeout", "10", "--find-links", str(wheelhouse), str(wheel)],
                       cwd=outside, env=env, check=True, timeout=args.install_timeout)
        # Prove imports are coming from this venv, rather than the checkout or caller's environment.
        code = ("import billgen, pathlib, sys; "
                "p=pathlib.Path(billgen.__file__).resolve(); "
                "assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()), str(p); print(p)")
        subprocess.run([str(python), "-I", "-c", code], cwd=outside, env=env,
                       check=True, timeout=15)
        env["BILL_GEN_DATA_DIR"] = str(root / "data")
        log_path = root / "server.log"
        process = None
        try:
            with log_path.open("w+b") as log:
                process = subprocess.Popen([str(entrypoint), "serve", "--host", "127.0.0.1", "--port", "0"],
                                           cwd=outside, env=env, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + args.startup_timeout
                base_url = None
                while time.monotonic() < deadline:
                    text = log_path.read_text(errors="replace")
                    match = re.search(r"BillGen: (http://127\.0\.0\.1:[0-9]+)", text)
                    if match:
                        base_url = match[1]
                        break
                    if process.poll() is not None:
                        raise SmokeError(f"billgen exited with status {process.returncode}")
                    time.sleep(0.05)
                if base_url is None:
                    raise SmokeError(f"billgen port announcement timed out after {args.startup_timeout}s")
                wait_ready(base_url, startup_timeout=max(0.01, deadline - time.monotonic()),
                           alive=lambda: process.poll() is None)
                result = run_smoke(base_url, timeout=args.request_timeout, template_id=args.template)
                print(json.dumps({"mode": "installed-wheel", "wheel": wheel.name, **result}))
        except BaseException:
            if log_path.exists():
                print("--- billgen server log ---\n" + log_path.read_text(errors="replace")[-24000:], file=sys.stderr)
            raise
        finally:
            stop_process(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--wheelhouse", type=Path, help="offline dependencies; defaults to wheel directory")
    parser.add_argument("--template", default="boa-personal")
    parser.add_argument("--install-timeout", type=positive_timeout, default=120)
    parser.add_argument("--startup-timeout", type=positive_timeout, default=30)
    parser.add_argument("--request-timeout", type=positive_timeout, default=60)
    args = parser.parse_args()
    try:
        smoke(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"installed-wheel smoke FAILED: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
