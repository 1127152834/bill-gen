# billgen

合成对账单 / 账单版式生成器：13 个银行模板，每个模板 = 一套版式引擎 + 一个浏览器编辑器，
输出带 `SAMPLE` 水印的示例 PDF。**不与任何银行系统通信，不读取真实交易，只渲染 JSON 里的假数据。**

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/assets.py hydrate      # 首次：拉取 LFS 资产（~113s）
.venv/bin/billgen serve --port 8800             # 单服务：浏览器打开 http://127.0.0.1:8800
.venv/bin/billgen list                          # 看 13 个模板
.venv/bin/billgen render boa-personal -o out.pdf
.venv/bin/billgen render --all --outdir _build   # 一次渲染全部后端模板
.venv/bin/python -m pytest tests -q              # 全量契约、渲染与状态隔离回归
.venv/bin/billgen parity verify                  # 版式指纹是否仍与基线一致
.venv/bin/ruff check .                           # 风格（遗留单行代码已按文件豁免）
.venv/bin/python -m mypy                         # 类型（spec / registry / paths）
```

## 浏览器工作台

首页提供统一的模板导航、结构化表单和 JSON 高级编辑、分页预览及 PDF / JSON 导入导出。
草稿按模板保存在当前浏览器；统一工作台的草稿与旧专属编辑器的服务端文件独立。
需要历史专属操作时，可通过「完整模板编辑器」进入原编辑器，原操作保持可用。
Alliant / Ally 保留浏览器内生成方式，并嵌入统一工作台。
前端为本地静态 HTML / CSS / JavaScript，无外部 CDN 或额外 Node 构建步骤。

## 目录结构

```
billgen/
├── src/billgen/
│   ├── spec.py            TemplateSpec / RenderOptions / RenderResult（唯一数据契约）
│   ├── registry.py        13 条模板注册表（唯一的模板清单）
│   ├── paths.py           资产与可写数据定位（含旧路径搬迁映射）
│   ├── cli.py             billgen 命令行（list / render / parity）
│   ├── engine/
│   │   ├── loader.py      内置主题规范包导入，外部旧式主题兼容隔离
│   │   ├── adapters.py    4 种历史入口签名 → 统一 Renderer
│   │   └── base.py        Renderer 协议 + RenderError
│   ├── parity/baseline.py 渲染指纹记录与比对（重构等价性判据）
│   ├── synth/             场景账本 → 模板数据 → PDF（见 docs/m1-synth.md）
│   │   ├── ledger.py       LedgerDocument：一期一账户、自洽余额链
│   │   ├── money.py        金额/日期/账号/路由号派生
│   │   ├── bridge.py       build_data / render / fingerprint / normalize_id
│   │   ├── lint.py         渲染前自检（闭合、丢行、账户类型）
│   │   └── adapters/       11 个服务端模板的字段映射
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
| `mercury` / `capitalone` / `sofi` | Mercury / Capital One / SoFi | 从零绘制（显式请求上下文） | `themes/<t>/…:build` |
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

所有 Python 模板的渲染器实现 `render(data, opts) -> RenderResult`，每次渲染深拷贝输入。
适配层吸收历史入口签名差异；Mercury / Capital One / SoFi 已使用
`build(meta=None, *, data=None, watermark=None)`，AE 使用显式文档上下文。
页面布局和默认渲染结果保持基线一致，模块默认数据不再作为请求交换区。

`RenderOptions` 接通水印和适用模板的字体策略；统一 Web API 的 `page_dpi` 控制 PNG
预览分辨率，AE / Chase / US Bank 支持加密导出。预览默认未加密；导出默认仅 AE 加密，
与历史默认策略一致。其他引擎显式请求加密返回 `unsupported_option`，而非静默忽略。
详见 [P2 执行记录](docs/p2-state-isolation.md)、[P3 执行记录](docs/p3-single-service.md) 与 [P4 执行记录](docs/p4-engine-consolidation.md)。

## Web 服务与 API

```bash
billgen serve --host 127.0.0.1 --port 8800
# 旧入口仍可使用：HOST=127.0.0.1 GATE_PORT=8800 python run.py
```

首页保留 10 个编辑器入口、覆盖全部 13 个模板。同进程直接挂载旧 handler，无内部端口、
无子进程、无 Referer 路由；旧页面保持原表单与操作。统一 API 独立调用 registry 渲染器：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/healthz` | 存活检查 |
| GET | `/api/v1/templates` | 模板能力和默认选项 |
| GET | `/api/v1/templates/{id}/data` | 默认数据 |
| POST | `/api/v1/templates/{id}/preview` | 创建预览产物 |
| POST | `/api/v1/templates/{id}/export` | 创建导出产物 |
| GET | `/api/v1/jobs/{job_id}` | 产物信息与下载地址 |
| GET | `/api/v1/jobs/{job_id}/document.pdf` | PDF 下载 |
| GET | `/api/v1/jobs/{job_id}/pages/{index}.png` | PNG，页索引从 0 开始 |
| GET | `/api/v1/synth/capabilities` | 可直出账本的模板与账户类型 |
| POST | `/api/v1/synth/render` | 场景结果包 → 模板数据 → PDF |

```bash
curl -sS http://127.0.0.1:8800/api/v1/templates/mercury/preview \
  -H 'Content-Type: application/json' \
  -d '{"options":{"page_dpi":110}}'
# 返回 job_id、页数、元数据和 links；按 links.pdf / links.pages 下载产物。
```

运行数据默认位于平台用户数据目录，可用绝对路径 `BILL_GEN_DATA_DIR` 覆盖。
启动只初始化缺失示例及 reset 备份，不覆盖已有文件。统一 API 产物在有界内存缓存中保留
15 分钟，最多 32 个任务 / 128 MiB；服务重启后清空。旧编辑器仍使用共享项目工作区，
不是多租户服务。详见 P3 文档中的隔离与兼容边界。

## 场景账本 → 账单 PDF

`flow/`（原 bill-gen-game）负责「数据有多可信」，`billgen.synth` 负责「版式有多像真的」：

```bash
billgen synth render chase-personal --from result.json -o out.pdf
billgen synth render wellsfargo --from result.json --account checking --cycle 0 -o wf.pdf
```

```python
from billgen.synth import from_game_result, render

ledger = from_game_result(payload, account="checking", cycle=-1)  # -1 = 最后一期
result = render("boa-personal", ledger)                          # result.pdf / result.pages
```

- 同一份账本重复渲染字节一致（trailer `/ID` 归一化），`fingerprint(pdf)` 给版式指纹；
- 渲染前跑账本自检：期初 + Σ贷 − Σ借 = 期末、不丢行、账户类型匹配，任何一条不通过就报
  `SynthLintError`，不产出半对的 PDF；
- 样例标记统一保留：可见 `SAMPLE` 水印 + PDF 元数据 `SAMPLE — NOT A REAL BANK STATEMENT`。

同一能力也走 HTTP 与工作台：

```bash
curl -sS http://127.0.0.1:8800/api/v1/synth/render \
  -H 'Content-Type: application/json' \
  -d '{"template":"boa-personal","result":'"$(cat result.json)"',"cycle":0,"watermark":true}'
# 201 返回 pages / links / ledger（账户、银行、账期、行数、期初期末）；links.pdf 直接下载。
```

工作台顶栏「场景导入」按钮做同一件事：粘贴或选择结果包、挑模板与账期，渲染完成后
模板数据会载入左侧表单，可继续微调再导出。默认会把版式里烙死的真实行标、行址、客服电话、
域名与邮编整套换成场景银行自己的派生值（`--no-brand` / `{"brand": false}` 可关闭），
文本层残留由 `tools/brand_audit.py` 兜底。细节见 [场景账本桥接](docs/m1-synth.md)、
[品牌替换包](docs/m2-branding.md)、[联系信息槽位化](docs/m3-contact.md) 与
[促销文案与商品名](docs/m4-promo-copy.md)、[MCP 服务](docs/m6-mcp.md) 与 [LangGraph → MCP 桥](docs/m7-langgraph-mcp.md)。

## 等价性怎么保证

`billgen parity` 对 11 个 Python 模板检查 `pages / size / text_sha256_16 / text_chars / fonts`；
对两个纯前端模板硬校验资源清单与入口/依赖存在性（不是浏览器导出等价证明）。
AE 与三上下文引擎另有原版逐页像素 golden 回归。重构各阶段都保持 `parity verify` PASS：

```
$ billgen parity verify
[note] note usbank-personal.bytes: 219290 -> 219291 (+1)   # 字节数允许 ±1 浮动
结论：PASS 基线一致
```

P1（目录搬迁 + 接口统一）、P2（请求上下文 + 状态隔离）、P3（单服务与统一 API）及
P4（公共能力抽取 + 规范包导入 + C1 字体子集）均通过该判据。P4 另以原实现快照验证
US Bank / Chase 的默认、空交易、多页和回放数据；C1 验证默认、CJK 与多页样例。
C1 通过仅替换嵌入字体流缩小输出，保持字体名、ToUnicode、宽度表与页面内容不变；
历史探针样例由 10,624,694 B 降至 612,891 B（约减少 94.2%），生成耗时略增。

## 兼容层

- `runtime_paths.py` 仍然可用，`data_path("富国/statement_template/editor/data.json")`
  会被翻译到 `src/billgen/themes/wellsfargo/editor/data.json`。
- `python3 src/billgen/themes/boa/generate.py` 这类「直接执行单个模板」老用法仍可产出 PDF。
- `run.py` 已改为单服务兼容入口，保留 `HOST` / `GATE_PORT`；旧的 8 子进程代理编排已移除。

## 工程门禁与交付

```bash
make PYTHON=.venv/bin/python check       # 测试/静态检查/严格环境指纹回归
make PYTHON=.venv/bin/python smoke-wheel # 构建后在独立 venv 离线安装并测 HTTP
make PYTHON=.venv/bin/python IMAGE=billgen:local smoke-image
```

先用 `-c constraints-ci.txt -e '.[dev]'` 安装已验证工具链；构建工具也包含在 dev extra，
wheel 资源测试不再因缺少构建器而默认跳过。CI 包含 Python 3.11–3.14 矩阵及独立交付检查，
只上传构建产物、不发布。严格回归接受已记录的渲染器/字体 profile，未知环境明确失败；
历史 macOS parity 与受控 Linux 回归分开维护。详见 [发布与验证步骤](docs/releasing.md) 和 [P5 执行记录](docs/p5-delivery.md)。

## 阶段记录

| 阶段 | 内容 |
|---|---|
| P0 ✅ | 基线探针 + 现状体检报告 |
| P1 ✅ | src 布局、目录搬迁、统一接口、注册表、CLI、parity 门禁 |
| P2 ✅ | 四引擎显式请求上下文，编辑器状态对象与完整串行窗口，AE 浏览器会话隔离 |
| P3 ✅ | 单服务入口、registry API、有界预览/导出产物、旧编辑器兼容挂载、wheel/Docker 验证 |
| P4 ✅ | Chase / US Bank 公共能力抽取（保留变体布局）、规范包导入、C1 保真字体子集 |
| P5 ✅ | CI 版本矩阵、环境指纹黄金回归、wheel/sdist 构建、独立安装和容器烟测、发布步骤（未发布） |
| M0 ✅ | 场景生成器（bill-gen-game）并入 `flow/`，离线 `LLM_FAKE=1` 全链路跑通 |
| M1 ✅ | `billgen.synth`：账本规范、11 个模板适配器、渲染前自检、CLI `synth render` |
| M2 ✅ | 品牌替换包：logo 物理替换 + amex/chase 品牌字段数据驱动 + `tools/brand_audit.py` 审计门禁 |
| M3 ✅ | 联系信息槽位化：行址 / 客服电话 / 域名 / 邮编按账本派生，正文固定串原位替换 |
| M4 ✅ | 促销文案与商品名：`Balance Connect™`/`Business Fundamentals®`/`SmartAsset`/`Merrill` 等换成场景银行自有产品 |
| M5 ✅ | 流水行语法：真账单拆解（`docs/statement-rows.md`）+ `synth/refs.py` 号生成 + 11 个模板的槽位落位（ACH trace / Zelle conf / Imad / Trn / CATEGORY / Type 词表 / 支票号 / BOA 描述符） |
| M6 ✅ | MCP 服务：`billgen mcp` 把模板契约 / 校验 / 渲染暴露成工具，Agent 自己填表出片 |
| M7 ✅ | LangGraph → MCP 桥：`billgen` 图上节点用 `mcp` 客户端拉契约、让模型填表、校验失败回灌重试、签发 PDF 并写进 `result.renders` |
| M10 ✅ | 常驻连接 + 模板自动推荐 + 单一镜像部署：`McpPool` 复用 Python 子进程，`BILLGEN_TEMPLATES=auto` 由推荐器打分选版式，`Dockerfile` 两段构建把 flow 与 billgen 装进同一个镜像（[docs/m10-pool-and-auto-templates.md](docs/m10-pool-and-auto-templates.md)） |
| M8 ✅ | 真实化：文档画像（`synth/profile.py`，按家族写 Producer/Creator + 周期后 1~3 天的出件时间）、入账日保险（ACH/费用只落工作日）、收单方前缀（`SQ *`/`TST* `/`PAYPAL *`）、chase 条码与 US Bank IMB 派生（`docs/m8-realism.md`） |
| M9 ✅ | 文本层清零：sofi 法务段落改「品牌态重排」、usbank 跨行散文与争议地址换字，`audit.KNOWN_GAPS` 归空（`docs/m3-contact.md` 第 5 节） |
