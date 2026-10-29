# billgen

合成对账单 / 账单版式生成器：13 个银行模板，每个模板 = 一套版式引擎 + 一个浏览器编辑器，
输出带 `SAMPLE` 水印的示例 PDF。**不与任何银行系统通信，不读取真实交易，只渲染 JSON 里的假数据。**

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/assets.py hydrate      # 首次：拉取 LFS 资产（~113s）
.venv/bin/billgen list                          # 看 13 个模板
.venv/bin/billgen render boa-personal -o out.pdf
.venv/bin/billgen render --all --outdir _build   # 一次渲染全部后端模板
.venv/bin/python -m pytest tests -q              # 92 个测试
.venv/bin/billgen parity verify                  # 版式指纹是否仍与基线一致
```

## 目录结构

```
billgen/
├── src/billgen/
│   ├── spec.py            TemplateSpec / RenderOptions / RenderResult（唯一数据契约）
│   ├── registry.py        13 条模板注册表（唯一的模板清单）
│   ├── paths.py           资产与可写数据定位（含旧路径搬迁映射）
│   ├── cli.py             billgen 命令行（list / render / parity）
│   ├── engine/
│   │   ├── loader.py      ThemeLoader：同名模块按主题隔离加载
│   │   ├── adapters.py    4 种历史入口签名 → 统一 Renderer
│   │   └── base.py        Renderer 协议 + RenderError
│   ├── parity/baseline.py 渲染指纹记录与比对（重构等价性判据）
│   └── themes/            13 个模板（版式引擎 + 编辑器 + 资产）
│       ├── boa/  amex/  wellsfargo/  mercury/  capitalone/  sofi/
│       ├── chase/  usbank/               底稿重放型
│       └── web/alliant/  web/ally/       纯前端
├── scripts/               assets.py（LFS 资产）/ seed_examples.py / extract_private.py
├── tests/                 契约 + 冒烟 + 确定性 + 既有单测
├── examples/              13 份示例数据
└── runtime_paths.py       兼容层（旧脚本 `from runtime_paths import data_path`）
```

## 13 个模板

| id | 名称 | 范式 | 入口 |
|---|---|---|---|
| `boa-personal` / `boa-business` | Bank of America | 从零绘制 | `themes/boa/generate.py:build_pdf` |
| `wellsfargo` | Wells Fargo | 从零绘制 | `themes/wellsfargo/generate.py:build_pdf` |
| `amex` | American Express | 从零绘制 | `themes/amex/generate_sample_statement.py:build_pdf` |
| `mercury` / `capitalone` / `sofi` | Mercury / Capital One / SoFi | 从零绘制（模块级 `DATA`） | `themes/<t>/…:build` |
| `chase-personal` / `chase-business` | Chase | 底稿重放 | `themes/chase/generate_personal.py` / `generate_business.py` |
| `usbank-personal` / `usbank-business` | US Bank | 底稿重放 | `themes/usbank/generate_us.py` / `generate_biz.py` |
| `alliant` / `ally` | Alliant / Ally | 纯前端 pdf-lib | `themes/web/*/index.html` |

「底稿重放」= 打开 `assets/*_prepared.pdf`，按 `layout_*.json` 的 span 表原位替换文本；
「从零绘制」= 用坐标与字体直接 `insert_text` 画每一页。

## 统一接口

```python
from billgen import registry

renderer = registry.get_renderer("boa-personal")
result = renderer.render(renderer.default_data())   # RenderResult(pdf=bytes, pages=int, elapsed_ms=int)
open("out.pdf", "wb").write(result.pdf)
```

所有模板的渲染器都实现 `render(data, opts) -> RenderResult`，`opts` 是 `RenderOptions`
（水印 / 加密 / 字体策略 / 预览 DPI）。历史入口签名（`watermark` vs `with_watermark`、
返回 `Document` vs 写文件、模块级 `DATA` 注入）由 `engine/adapters.py` 吸收，
版式代码本身一行未改。

## 等价性怎么保证

`billgen parity` 记录 13 个模板的 `pages / size / text_sha256_16 / text_chars / fonts`，
这些值在两次运行间完全稳定。重构的每个阶段都必须让 `parity verify` 保持 PASS：

```
$ billgen parity verify
[note] note usbank-personal.bytes: 219290 -> 219291 (+1)   # 字节数允许 ±1 浮动
结论：PASS 基线一致
```

P1（目录搬迁 + 接口统一，本文档描述的状态）已通过该判据。

## 兼容层

- `runtime_paths.py` 仍然可用，`data_path("富国/statement_template/editor/data.json")`
  会被翻译到 `src/billgen/themes/wellsfargo/editor/data.json`。
- `python3 src/billgen/themes/boa/generate.py` 这类「直接执行单个模板」老用法仍可产出 PDF。
- 旧的服务编排（`run.py` + 8 个子进程 + 网关）仍在仓库根，将在 P3 由统一的 `web/` 服务取代。

## 后续阶段

| 阶段 | 内容 |
|---|---|
| P0 ✅ | 基线探针 + 现状体检报告 |
| P1 ✅ | src 布局、目录搬迁、统一接口、注册表、CLI、parity 门禁 |
| P2 | 消灭全局态：`STATE` / `DATA` / `WATERMARK` / `META` 改为显式参数 |
| P3 | 单一 Web 服务：按 `registry` 路由，编辑器与渲染器解耦 |
| P4 | 消除重复：Chase / US Bank 的双胞胎引擎合并，C1 字体体积（10.6MB）治理 |
| P5 | 交付工程：CI、镜像、发布、文档 |
