"""账单生成器的模型 / 检索凭据配置。

这里的配置只装生成引擎真正要用的四样东西（模型端点、模型 Key、模型名、Tavily Key），
字段固定、不做局部更新、没有默认的假数据（见 ``docs/DECISIONS.md`` 决策 9：只走真实模型）。

优先级：**环境变量 > 文件**。

* 四个环境变量（``BILLGEN_LLM_BASE_URL`` / ``BILLGEN_LLM_API_KEY`` / ``BILLGEN_LLM_MODEL`` /
  ``TAVILY_API_KEY``）全齐时连文件都不读——容器部署通常只给环境变量，本地开发才落文件。
* 只给了一部分时，剩下的从文件补；文件也缺就是配置错误，绝不静默降级成假数据。

文件里是明文 Key：写入一律 ``0600`` + 原子替换（先写临时文件、fsync、再 ``os.replace``），
半截写入或掉电都不会留下坏文件。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ConfigError",
    "LlmConfig",
    "load_config",
    "save_config",
]

#: 本地开发的默认位置；``BILLGEN_LLM_CONFIG`` 可改。相对路径按进程工作目录解析。
DEFAULT_CONFIG_PATH = Path(os.environ.get("BILLGEN_LLM_CONFIG", ".data/billgen/llm.json"))

#: 字段名 → 覆盖它的环境变量。
ENV_FIELDS: dict[str, str] = {
    "base_url": "BILLGEN_LLM_BASE_URL",
    "api_key": "BILLGEN_LLM_API_KEY",
    "model": "BILLGEN_LLM_MODEL",
    "tavily_key": "TAVILY_API_KEY",
}

#: 必填字段（顺序即报错里的展示顺序）。
REQUIRED_FIELDS: tuple[str, ...] = ("base_url", "api_key", "model", "tavily_key")

_FIELD_HINT = "字段 " + "/".join(REQUIRED_FIELDS)


class ConfigError(ValueError):
    """模型配置缺失或不合法（缺文件、缺字段、JSON 坏掉）。"""


@dataclass(slots=True)
class LlmConfig:
    """一次生成需要的凭据与传输参数。

    ``timeout`` 是单次 HTTP 的超时（秒），``max_retries`` 是**失败之后**还能再试几次
    （所以最坏情况是 ``max_retries + 1`` 次请求）。
    """

    base_url: str
    api_key: str
    model: str
    tavily_key: str
    timeout: float = 180.0
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        """落盘用的完整字段（含可选字段）。"""
        return asdict(self)

    def masked(self) -> dict[str, Any]:
        """日志 / 报错用的视图：两个 Key 换成「有没有配」，其余原样。"""
        data = asdict(self)
        for name in ("api_key", "tavily_key"):
            data[name] = "***" if str(data.get(name) or "").strip() else ""
        return data


def _resolve(path: Path | str | None) -> Path:
    """解析配置文件路径：显式传入 > 环境变量（调用时读，改了就生效）> 默认值。"""
    if path is not None:
        return Path(path)
    env = os.environ.get("BILLGEN_LLM_CONFIG")
    return Path(env) if env else DEFAULT_CONFIG_PATH


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _env_values() -> dict[str, str]:
    """环境里真正给了值的字段（空串视为没给）。"""
    out: dict[str, str] = {}
    for field, name in ENV_FIELDS.items():
        value = _text(os.environ.get(name))
        if value:
            out[field] = value
    return out


def _read_file(target: Path) -> dict[str, Any]:
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"缺少模型配置：{target} 不存在，也没有齐全的环境变量（{_FIELD_HINT}）"
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"读不到模型配置：{target}（{exc.strerror or exc}；{_FIELD_HINT}）"
        ) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"模型配置不是合法 JSON：{target}（{exc}；{_FIELD_HINT}）") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"模型配置必须是 JSON 对象：{target}（{_FIELD_HINT}）")
    return data


def _number(data: dict[str, Any], name: str, default: float) -> float:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value) if value > 0 else default


def _count(data: dict[str, Any], name: str, default: int) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if value >= 0 else default


def load_config(path: Path | str | None = None) -> LlmConfig:
    """读配置：环境变量逐项覆盖文件；环境变量齐全时文件根本不读。

    缺文件、缺字段、``api_key`` 只有空白，都抛 :class:`ConfigError`，
    报错里列出缺的字段名，路径原样给出——用户能照着改。
    """
    target = _resolve(path)
    env = _env_values()
    data: dict[str, Any] = {}
    if len(env) < len(ENV_FIELDS):
        data = _read_file(target)
    data.update(env)

    missing = [name for name in REQUIRED_FIELDS if not _text(data.get(name))]
    if missing:
        detail = "、".join(missing)
        source = f"{target}" if len(env) < len(ENV_FIELDS) else "环境变量"
        raise ConfigError(f"缺少模型配置：{source} 里 {detail} 没填（{_FIELD_HINT}）")

    return LlmConfig(
        base_url=_text(data["base_url"]).rstrip("/"),
        api_key=_text(data["api_key"]),
        model=_text(data["model"]),
        tavily_key=_text(data["tavily_key"]),
        timeout=_number(data, "timeout", 180.0),
        max_retries=_count(data, "max_retries", 3),
    )


def save_config(cfg: LlmConfig, path: Path | str | None = None) -> Path:
    """原子写配置：目录自动创建，临时文件权限 0600，替换后 fsync 目录。

    返回最终路径，方便调用方回显「存到哪了」。
    """
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n"

    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    tmp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)  # mkstemp 已经是 0600，这里只保证不随 umask 漂移
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)
    return target


def _fsync_dir(directory: Path) -> None:
    """目录项也要落盘，否则刚改名的文件可能随掉电丢链接。"""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:  # 某些文件系统（或 Windows）不允许 open 目录，不阻断保存
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
