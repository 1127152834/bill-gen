#!/usr/bin/env python3
"""兼容旧启动命令；单进程服务，不再创建内部代理端口。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main():
    from billgen.server.http import serve

    return serve(os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("GATE_PORT", "8800")))


if __name__ == "__main__":
    raise SystemExit(main())
