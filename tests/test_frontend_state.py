"""Run the dependency-free browser state regression when Node is installed."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_state():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is optional; not installed")
    script = Path(__file__).with_name("frontend_state.mjs")
    result = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: frontend state regression" in result.stdout
