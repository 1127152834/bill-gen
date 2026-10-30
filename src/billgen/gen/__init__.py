"""账单 JSON 生成器的模型与检索客户端。

``flow/packages/core`` 的 Node 实现（``llm.ts`` / ``research.ts``）在这里缩成三个模块：

* :mod:`billgen.gen.config` —— 凭据（环境变量 > ``.data/billgen/llm.json``，0600 原子写）；
* :mod:`billgen.gen.llm` —— OpenAI 兼容 Chat Completions + JSON 修复重试 + token 记账；
* :mod:`billgen.gen.tavily` —— Tavily 检索 + 软配额（超限抛 :class:`TavilyQuotaExceeded` 让调用方降级）。

上面的 LangGraph 节点（normalize → planResearch → research → … → month_write）还没迁过来，
这一层只负责「能和真实模型、真实检索说话」，并且全部可打桩（``http`` 注入），单元测试不联网。
"""

from __future__ import annotations

from .config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    LlmConfig,
    load_config,
    save_config,
)
from .llm import (
    LlmBadJson,
    LlmClient,
    LlmError,
    LlmRefused,
    drop_code_fence,
)
from .tavily import (
    TavilyClient,
    TavilyError,
    TavilyQuotaExceeded,
    TavilyResult,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ConfigError",
    "LlmBadJson",
    "LlmClient",
    "LlmConfig",
    "LlmError",
    "LlmRefused",
    "TavilyClient",
    "TavilyError",
    "TavilyQuotaExceeded",
    "TavilyResult",
    "drop_code_fence",
    "load_config",
    "save_config",
]
