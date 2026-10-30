"""账单猎手的运行配置：模型接入、模板选择、渲染开关。

配置存在数据目录里（``hunter/settings.json``），前端可以改，改完由 :mod:`billgen.hunter.process`
重启子进程生效。密钥只写不读：读取时一律给出 ``has_*`` 布尔，避免把 key 回显到浏览器。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .. import paths

StructuredMode = Literal["json_schema", "function_calling", "json_mode"]
STRUCTURED_MODES: tuple[StructuredMode, ...] = ("json_schema", "function_calling", "json_mode")

SETTINGS_RELATIVE = "hunter/settings.json"
TEMPLATE_CHOICES = (
    "auto",
    "boa-personal",
    "boa-business",
    "chase-personal",
    "chase-business",
    "usbank-personal",
    "usbank-business",
    "wellsfargo",
    "mercury",
    "capitalone",
    "sofi",
)


class SettingsError(ValueError):
    """配置不合法（字段类型、枚举、范围）。"""


@dataclass(frozen=True, slots=True)
class HunterSettings:
    """一次生成需要的全部开关。默认值等于「离线夹具、虚构行名、自动挑模板」。"""

    llm_fake: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-6.1-sol"
    structured_mode: StructuredMode = "json_schema"
    tavily_api_key: str = ""
    bank_name: str = "Bank of America"
    templates: str = "auto"
    auto_limit: int = 2
    ai_fill: bool = True
    service_key: str = "devlocal"

    # ---- 派生状态 -------------------------------------------------------
    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def has_tavily_key(self) -> bool:
        return bool(self.tavily_api_key.strip())

    @property
    def model_ready(self) -> bool:
        """真实模型链路是否齐全（假模型永远齐全，只是离线）。"""
        return self.llm_fake or (bool(self.base_url.strip()) and self.has_api_key)

    @property
    def template_list(self) -> list[str]:
        return [part.strip() for part in self.templates.split(",") if part.strip()] or ["auto"]

    def public(self) -> dict[str, Any]:
        """给前端的视图：密钥替换成「有没有配」。"""
        data = asdict(self)
        for field in ("api_key", "tavily_api_key", "service_key"):
            data.pop(field, None)
        data.update(
            {
                "has_api_key": self.has_api_key,
                "has_tavily_key": self.has_tavily_key,
                "model_ready": self.model_ready,
                "structured_modes": list(STRUCTURED_MODES),
                "template_choices": list(TEMPLATE_CHOICES),
                "template_list": self.template_list,
            }
        )
        return data


def _env_flag(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip() not in {"0", "false", "False", "no", "off"}


def _env_int(value: str | None, default: int, *, low: int, high: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if low <= parsed <= high else default


def from_env(environ: dict[str, str] | None = None) -> HunterSettings:
    """首次运行时用容器环境变量播种配置（之后以文件为准）。"""
    env = dict(os.environ if environ is None else environ)
    base = HunterSettings(
        llm_fake=_env_flag(env.get("LLM_FAKE"), True),
        base_url=env.get("LLM_BASE_URL", ""),
        api_key=env.get("LLM_API_KEY", ""),
        model=env.get("LLM_MODEL") or "gpt-6.1-sol",
        structured_mode=_structured(env.get("LLM_STRUCTURED_MODE")),
        tavily_api_key=env.get("TAVILY_API_KEY", ""),
        bank_name=env.get("BANK_DISPLAY_NAME") or "Bank of America",
        templates=env.get("BILLGEN_TEMPLATES") or "auto",
        auto_limit=_env_int(env.get("BILLGEN_AUTO_LIMIT"), 2, low=1, high=11),
        ai_fill=_env_flag(env.get("BILLGEN_AI_FILL"), True),
        service_key=env.get("API_KEY") or "devlocal",
    )
    return base


def _structured(value: str | None) -> StructuredMode:
    text = (value or "").strip()
    return text if text in STRUCTURED_MODES else "json_schema"  # type: ignore[return-value]


def _as_bool(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise SettingsError(f"{field} 需要布尔值")


def _as_text(field: str, value: Any, *, limit: int = 400) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SettingsError(f"{field} 需要字符串")
    text = value.strip()
    if len(text) > limit:
        raise SettingsError(f"{field} 过长（上限 {limit} 字符）")
    return text


def apply_patch(current: HunterSettings, patch: dict[str, Any]) -> HunterSettings:
    """PATCH 语义：缺省字段保持原值，密钥传空串表示「不修改」。"""
    if not isinstance(patch, dict):
        raise SettingsError("配置必须是 JSON 对象")
    unknown = set(patch) - set(asdict(current))
    if unknown:
        raise SettingsError("未知字段：" + ", ".join(sorted(unknown)))
    update: dict[str, Any] = {}
    for field, value in patch.items():
        if field in ("api_key", "tavily_api_key", "service_key"):
            text = _as_text(field, value, limit=200)
            if text:
                update[field] = text
            continue
        if field in ("llm_fake", "ai_fill"):
            update[field] = _as_bool(field, value)
        elif field == "auto_limit":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 11:
                raise SettingsError("auto_limit 需要 1~11 的整数")
            update[field] = value
        elif field == "structured_mode":
            text = _as_text(field, value, limit=40)
            if text not in STRUCTURED_MODES:
                raise SettingsError("structured_mode 取值为 " + " / ".join(STRUCTURED_MODES))
            update[field] = text
        elif field == "templates":
            text = _as_text(field, value, limit=200)
            names = [part.strip() for part in text.split(",") if part.strip()]
            if not names:
                raise SettingsError("templates 至少要有一个模板")
            bad = [name for name in names if name not in TEMPLATE_CHOICES]
            if bad:
                raise SettingsError("未知模板：" + ", ".join(bad))
            update[field] = ",".join(names)
        elif field == "base_url":
            text = _as_text(field, value, limit=300)
            if text and not text.startswith(("http://", "https://")):
                raise SettingsError("base_url 需要 http(s):// 开头")
            update[field] = text
        else:
            update[field] = _as_text(field, value, limit=120)
    merged = replace(current, **update)
    if merged.base_url.endswith("/"):
        merged = replace(merged, base_url=merged.base_url.rstrip("/"))
    return merged


class SettingsStore:
    """配置文件的读写；坏文件不阻断启动，回落到环境变量播种值。"""

    def __init__(self, path: Path | None = None, *, environ: dict[str, str] | None = None) -> None:
        self._path = Path(path) if path else Path(paths.data_path(SETTINGS_RELATIVE))
        self._environ = environ

    @property
    def path(self) -> Path:
        return self._path

    def defaults(self) -> HunterSettings:
        return from_env(self._environ)

    def load(self) -> HunterSettings:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self.defaults()
        if not isinstance(raw, dict):
            return self.defaults()
        stored = {key: value for key, value in raw.items() if key in asdict(HunterSettings())}
        try:
            return apply_patch(self.defaults(), stored)
        except SettingsError:
            return self.defaults()

    def save(self, settings: HunterSettings) -> HunterSettings:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n"
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self._path)
        return settings

    def patch(self, patch: dict[str, Any]) -> HunterSettings:
        return self.save(apply_patch(self.load(), patch))


def mcp_command() -> str:
    """flow 拉起 MCP 子进程时用的命令。"""
    explicit = os.environ.get("BILLGEN_MCP_CMD", "").strip()
    if explicit:
        return explicit
    found = shutil.which("billgen")
    if found:
        return found
    # flow 用 spawn(command, ["mcp"])，只接受单个可执行文件，不能带参数
    sibling = Path(sys.executable).with_name("billgen")
    if sibling.is_file():
        return sibling.as_posix()
    return Path(sys.executable).as_posix()


def to_env(settings: HunterSettings, *, port: int, flow_root: Path) -> dict[str, str]:
    """转换成 flow 进程认识的环境变量。"""
    env = {
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "LLM_FAKE": "1" if settings.llm_fake else "0",
        "LLM_MODEL": settings.model,
        "LLM_STRUCTURED_MODE": settings.structured_mode,
        "API_KEY": settings.service_key,
        "BANK_DISPLAY_NAME": settings.bank_name,
        "BILLGEN_MCP_CMD": mcp_command(),
        "BILLGEN_TEMPLATES": settings.templates,
        "BILLGEN_AUTO_LIMIT": str(settings.auto_limit),
        "BILLGEN_AI_FILL": "1" if settings.ai_fill else "0",
        "BILLGEN_OUT": os.environ.get("BILLGEN_OUT") or paths.data_path("hunter/renders"),
        "DATABASE_PATH": os.environ.get("BILLGEN_HUNTER_DB") or paths.data_path("hunter/app.db"),
        "WEB_DIST": os.environ.get("WEB_DIST") or "apps/web/dist",
        "NODE_ENV": "production",
        # 猎手的逐步日志（logfmt）默认直接打到自己的 stdout，供 docker logs / 前台查看。
        "LOG_PROGRESS": os.environ.get("LOG_PROGRESS", "1"),
        "LOG_RESEARCH": os.environ.get("LOG_RESEARCH", "0"),
    }
    if settings.base_url:
        env["LLM_BASE_URL"] = settings.base_url
    if settings.api_key:
        env["LLM_API_KEY"] = settings.api_key
    if settings.tavily_api_key:
        env["TAVILY_API_KEY"] = settings.tavily_api_key
    env["FLOW_ROOT"] = flow_root.as_posix()
    return env
