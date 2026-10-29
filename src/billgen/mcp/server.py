"""billgen 的 MCP 服务：把「渲染一张账单」做成 LLM 能自己调的工具。

设计要点
--------
* LLM 不做像素，只做**数据**：`template_contract` 给它骨架 + 类型 + 说明 + 规则，
  `render` 给它 PDF，`validate` 给它结构化的错误，形成「填 → 校验 → 改 → 再校验」的闭环。
* 全部工具不联网、不写数据库，输出落在 `--out` 目录里，返回**文件路径 + 指纹**。
* 只暴露渲染能力；「账本怎么造」是 flow 那边 LangGraph 的事。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import fitz

from .. import audit, branding, paths, registry, synth
from ..engine.base import RenderError
from ..spec import RenderOptions
from . import contract as contracts

__all__ = ["build", "main", "OUTPUT_DIR_ENV"]

OUTPUT_DIR_ENV = "BILLGEN_MCP_OUT"


def _out_dir(explicit: str | None) -> Path:
    if explicit:
        target = Path(explicit).expanduser().resolve()
    elif os.environ.get(OUTPUT_DIR_ENV):
        target = Path(os.environ[OUTPUT_DIR_ENV]).expanduser().resolve()
    else:
        target = Path(tempfile.gettempdir()) / "billgen-mcp"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _digest(pdf: bytes) -> str:
    return hashlib.sha256(pdf).hexdigest()


# 这些模板的展示列本来就是格式化字符串（含千分位），不做裸金额检查
_FORMATTED_TEMPLATES = {"wellsfargo", "usbank-personal", "usbank-business"}
_FORMATTED_SUBTREES = ("summary", "totals", "daily", "daily_balance", "stats", "msf", "atm")


def _money_problems(data: Any, template_id: str = "") -> list[str]:
    """扫一遍所有字符串金额，挑出被格式化坏了的那些。"""
    problems: list[str] = []
    stack: list[tuple[str, Any]] = [("", data)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, dict):
            stack.extend((f"{path}.{k}" if path else str(k), v) for k, v in value.items())
        elif isinstance(value, list):
            stack.extend((f"{path}[{i}]", v) for i, v in enumerate(value))
        elif isinstance(value, str) and path:
            if template_id in _FORMATTED_TEMPLATES or any(
                part in path for part in _FORMATTED_SUBTREES
            ):
                continue
            leaf = path.rsplit(".", 1)[-1].split("[")[0]
            if leaf in {"amount", "deposit", "withdrawal", "balance", "begin_balance"} and (
                "$" in value or "," in value
            ):
                problems.append(f"{path} = {value!r}：金额不能带 $ 或千分位")
    return problems


def _branded(pdf: bytes, template_id: str, data: Any) -> bytes:
    """把版式里烙死的真实行标/行址/电话换成数据里那家银行自己的。"""
    if not isinstance(data, dict) or template_id not in branding.PLACEMENTS:
        return pdf
    brand = branding.brand_from_data(data)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        branding.apply(doc, template_id, brand)
        return doc.tobytes(garbage=3, deflate=True)


def _validate(template_id: str, data: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return {"ok": False, "errors": ["data 必须是对象"], "warnings": []}

    try:
        # 骨架里由 _scaffold 补的键（bank / product / promo）不是硬要求：适配器不写也能渲染。
        required = contracts.required_keys(template_id)
    except ValueError:
        required = []
    for key in required:
        if key not in data:
            errors.append(f"缺少必需字段 {key}")
    bank = data.get("bank") if isinstance(data.get("bank"), dict) else {}
    if bank:
        for key in ("name", "domain"):
            if not str(bank.get(key, "")).strip():
                errors.append(f"bank.{key} 不能为空")
        # 富国的数据契约把法人名放 legal_na，其余模板叫 legal。
        if not str(bank.get("legal") or bank.get("legal_na") or "").strip():
            errors.append("bank.legal 不能为空")
    if data.get("watermark") is False:
        warnings.append("watermark=false：合成的账单必须保留 SAMPLE 水印")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if meta and "SAMPLE" not in str(meta.get("title", "")):
        warnings.append("metadata.title 缺少 SAMPLE 声明")

    errors.extend(_money_problems(data, template_id))

    pdf: bytes | None = None
    pages = 0
    try:
        renderer = registry.get_renderer(template_id)
        result = renderer.render(data, renderer.spec.default_options)
        pdf, pages = result.pdf, result.pages
    except RenderError as err:
        errors.append(f"渲染失败：{err}")
    except Exception as err:  # noqa: BLE001 - 引擎的 KeyError 也算数据问题，不该让工具崩
        errors.append(f"渲染失败：{type(err).__name__}: {err}")

    # 先在「未重绘」的原始渲染上取文本层：品牌重绘会按要求擦掉行标/行址，
    # 擦完再扫就等于自己把自己的证据抹了。
    if pdf is not None:
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            flat = audit.normalize(" ".join(page.get_text() for page in doc))
        placeholders = audit.placeholder_gaps(template_id, audit.scan_placeholders(flat))
        if placeholders:
            errors.append(f"文本层仍有虚构占位行名：{placeholders}")
        elif not flat.strip():
            warnings.append("文本层为空：渲染结果可能只是一张图")

    if pdf is not None:
        pdf = _branded(pdf, template_id, data)

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "pages": pages, "bytes": len(pdf) if pdf else 0}


def build(*, out_dir: str | None = None):  # noqa: ANN201 - 返回 FastMCP，避免顶层依赖
    from mcp.server.fastmcp import FastMCP

    out = _out_dir(out_dir)
    server = FastMCP(
        name="billgen",
        instructions=(
            "合成账单渲染服务。先用 list_templates 选模板，用 template_contract 拿到骨架与字段说明，"
            "把数据填好（金额闭合、虚构银行身份、保留 SAMPLE 水印），再调用 render 出 PDF。"
            "validate 只做检查不落盘，用来在正式渲染前迭代。"
        ),
    )

    @server.tool(description="列出所有可服务端渲染的模板：id、显示名、可接受账户类型")
    def list_templates() -> dict[str, Any]:
        items = []
        for spec in registry.SPECS:
            if spec.kind == "static-web":
                continue
            accounts = ["checking"]
            if spec.id.endswith("-business") or "business" in spec.id or "biz" in spec.id:
                accounts = ["business_checking"]
            try:
                binding = synth.binding_for(spec.id)
                accounts = sorted(binding.accounts)
            except Exception:  # noqa: BLE001 - 没有 synth 绑定时退回默认
                pass
            items.append({
                "id": spec.id,
                "display_name": spec.display_name,
                "kind": spec.kind,
                "accounts": accounts,
            })
        return {"count": len(items), "templates": items}

    @server.tool(
        description=(
            "给定一本账本，按版式适配度给模板打分并取前 N 名（不渲染、不用模型）："
            "描述长度会不会截断、分页好不好看、账本需要的区块模板有没有。返回 score 与逐条 reasons。"
        )
    )
    def recommend_templates(result: dict[str, Any], account: str = "", cycle: int = -1,
                            bank_name: str = "", limit: int = 3) -> dict[str, Any]:
        try:
            # 这里是「按版式适配度打分」，本身不属于任何一家版式：传空模板，
            # 让账本走默认的日历月锚定逻辑（早先误写成 `template=template_id`，
            # 本函数根本没有这个参数，工具一调就 NameError）。
            ledger = synth.from_game_result(result, account=account or None, cycle=cycle,
                                            template="", bank_name=bank_name or None)
        except (KeyError, ValueError, synth.SynthLintError) as err:
            return {"ok": False, "errors": [str(err)], "candidates": []}
        ranked = synth.recommend(ledger, limit=max(1, min(int(limit), len(synth.BINDINGS))))
        return {
            "ok": True,
            "account": ledger.account.kind,
            "rows": len(ledger.rows),
            "candidates": [candidate.as_dict() for candidate in ranked],
            "all": [candidate.as_dict() for candidate in synth.score_all(ledger)],
        }

    @server.tool(description="取某模板的完整契约：data 骨架 + types + 每字段说明 + 规则 + 限额")
    def template_contract(template_id: str) -> dict[str, Any]:
        return contracts.contract(template_id)

    @server.tool(description="只校验数据能不能渲染，不落盘；返回 errors / warnings / 页数")
    def validate(template_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return _validate(template_id, data)

    @server.tool(description="渲染 PDF 并落盘；返回文件路径、页数、字节数、sha256、已知提示")
    def render(template_id: str, data: dict[str, Any], filename: str = "",
               watermark: bool = True, encrypt: bool = False) -> dict[str, Any]:
        renderer = registry.get_renderer(template_id)
        opts = RenderOptions(watermark=watermark, encrypt=encrypt,
                             font_policy=renderer.spec.default_options.font_policy)
        try:
            result = renderer.render(data, opts)
        except RenderError as err:
            return {"ok": False, "errors": [str(err)]}
        except Exception as err:  # noqa: BLE001
            return {"ok": False, "errors": [f"{type(err).__name__}: {err}"]}
        result = type(result)(pages=result.pages,
                              pdf=_branded(result.pdf, template_id, data),
                              hashes=result.hashes, elapsed_ms=result.elapsed_ms)
        name = filename.strip() or f"{template_id}.pdf"
        if not name.endswith(".pdf"):
            name += ".pdf"
        target = out / Path(name).name
        target.write_bytes(result.pdf)
        with fitz.open(stream=result.pdf, filetype="pdf") as doc:
            flat = audit.normalize(" ".join(page.get_text() for page in doc))
        placeholders = audit.placeholder_gaps(template_id, audit.scan_placeholders(flat))
        return {
            "ok": True,
            "path": str(target),
            "pages": result.pages,
            "bytes": len(result.pdf),
            "sha256": _digest(result.pdf),
            "brand_leftovers": placeholders,
        }

    @server.tool(description="把指定页渲染成 PNG 供肉眼检查；返回文件路径")
    def preview(template_id: str, data: dict[str, Any], pages: list[int] | None = None,
                dpi: int = 110) -> dict[str, Any]:
        renderer = registry.get_renderer(template_id)
        try:
            result = renderer.render(data, renderer.spec.default_options)
        except Exception as err:  # noqa: BLE001
            return {"ok": False, "errors": [f"{type(err).__name__}: {err}"], "files": []}
        result = type(result)(pages=result.pages, pdf=_branded(result.pdf, template_id, data),
                              hashes=result.hashes, elapsed_ms=result.elapsed_ms)
        files: list[str] = []
        wanted = pages or [1]
        with fitz.open(stream=result.pdf, filetype="pdf") as doc:
            for number in wanted:
                index = number - 1
                if not 0 <= index < doc.page_count:
                    continue
                png = out / f"{template_id}-p{number}.png"
                doc[index].get_pixmap(dpi=dpi).save(png)
                files.append(str(png))
        return {"ok": True, "files": files, "pages": result.pages}

    @server.tool(description="把 flow 的 GenerationResult 转成该模板的数据草稿（之后让 LLM 微调）")
    def draft_from_ledger(template_id: str, result: dict[str, Any], account: str = "",
                          cycle: int = -1, bank_name: str = "") -> dict[str, Any]:
        try:
            ledger = synth.from_game_result(result, account=account or None, cycle=cycle,
                                            template=template_id, bank_name=bank_name or None)
            data = synth.build_data(template_id, ledger)
        except (KeyError, ValueError, synth.SynthLintError) as err:
            return {"ok": False, "errors": [str(err)]}
        return {"ok": True, "data": data, "ledger": {
            "account": ledger.account.kind, "bank": ledger.bank.name,
            "period": [ledger.period.start.isoformat(), ledger.period.end.isoformat()],
            "rows": len(ledger.rows),
            "opening_cents": ledger.opening_cents, "closing_cents": ledger.closing_cents,
        }}

    return server


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="billgen MCP 服务（stdio）")
    ap.add_argument("--out", default=None, help=f"产物目录，默认 ${OUTPUT_DIR_ENV} 或系统临时目录")
    ap.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    args = ap.parse_args(argv)
    server = build(out_dir=args.out)
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# 供测试与脚本直接用的契约出口（不经过 MCP 传输层）
def contract_json(template_id: str) -> str:
    return json.dumps(contracts.contract(template_id), ensure_ascii=False, indent=2)


_ = paths  # 保持导入以便将来的数据目录相关工具使用
