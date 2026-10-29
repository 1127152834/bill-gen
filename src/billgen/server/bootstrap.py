"""安装包启动引导：将只读合成示例初始化到独立可写工作区。"""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

from billgen import paths


def _create_once(path: Path, content: bytes) -> bool:
    """独占创建，重复启动及并行启动均保留已有数据。"""
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        return False
    return True


def prepare_workspace() -> dict[str, Any]:
    """初始化工作区和旧编辑器 reset 备份；不启动进程、不覆盖文件。

    返回 ``{data_dir: str, seeded: int, backups: int}``。
    ``BILL_GEN_DATA_DIR`` 可指定绝对数据目录；默认使用平台用户数据目录。
    缺失的 .bak 取打包示例而非用户编辑值，已有 .bak 原样保留。
    """
    examples = files("billgen").joinpath("examples")
    # 源码运行也使用同一 manifest，wheel 内则读取随包资源。
    if not examples.joinpath("manifest.json").is_file():
        examples = paths.SRC.parent / "examples"
    manifest = json.loads(examples.joinpath("manifest.json").read_text(encoding="utf-8"))
    plan: list[tuple[Path, Path, bytes]] = []
    for entry in manifest["files"]:
        source, target = PurePosixPath(entry["source"]), PurePosixPath(entry["target"])
        if (
            source.is_absolute() or target.is_absolute()
            or ".." in source.parts or ".." in target.parts
            or "\\" in str(source) or "\\" in str(target)
            or len(source.parts) != 2 or source.parts[0] != "examples"
            or source.suffix != ".json" or target.suffix != ".json"
            or not target.name.startswith("data")
        ):
            raise ValueError("示例配置只允许 examples 内 JSON 和工作区数据 JSON")
        content = examples.joinpath(source.name).read_bytes()
        json.loads(content)
        destination = Path(paths.data_path(str(target)))
        backup = Path(paths.data_path(str(target) + ".bak"))
        plan.append((destination, backup, content))
    seeded = backups = 0
    for destination, backup, content in plan:
        seeded += _create_once(destination, content)
        backups += _create_once(backup, content)
    return {"data_dir": str(paths.user_data_dir()), "seeded": seeded, "backups": backups}
