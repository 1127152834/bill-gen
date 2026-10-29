"""bill-gen：合成对账单/账单版式生成器。

公开入口
--------
``billgen.spec``      模板描述与渲染选项（唯一契约）
``billgen.registry``  13 个模板的注册表
``billgen.paths``     运行期路径解析（含旧路径搬迁映射）
``billgen.cli``       ``billgen`` 命令行

兼容层
------
P1 阶段遗留引擎仍在用 ``from runtime_paths import data_path``。这里把
``runtime_paths`` 注册成 :mod:`billgen.paths` 的别名，使新旧两种写法指向同一份实现。
"""
from __future__ import annotations

import sys

from . import paths as _paths

__version__ = "2.0.0.dev0"

# 遗留引擎的旧导入名 → 真实实现（同一模块对象，不存在两份逻辑）
sys.modules.setdefault("runtime_paths", _paths)

__all__ = ["__version__", "paths"]
