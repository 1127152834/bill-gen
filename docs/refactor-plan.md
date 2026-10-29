# bill-gen 规范化重构方案

> 目标：**功能保持一致**（13 个模板入口的可渲染产物、可编辑字段、导出能力、HTTP 行为不变），
> 同时把「13 个快照目录 + 6 份复制粘贴的编辑器服务 + 8 个子进程 + 派生产物入库」
> 收敛成一个可测试、可扩展、可发布的项目。

本文的所有数字都来自仓库实测（见 §1.4 与 `baseline.json`），不是估算。

---

## 1. 现状扫描

### 1.1 它实际上是什么

一个「对账单版式生成器」集合：13 个模板 = HTML 表单编辑器 + Python/浏览器 PDF 引擎。
每个模板把一份 JSON 数据渲染成 PDF，可选 SAMPLE 水印、可选 RC4 加密、可选页预览 PNG。
不连银行、不读真实交易，产物是合成示例。

### 1.2 目录即交付物

```
bill-gen/
├── run.py                      # 319 行：8 个子进程 + 端口反代网关 + 内嵌切换页
├── runtime_paths.py             # 41 行：data_path/font_path/cjk_font_path
├── AE/          editor.py(974) + generate_sample_statement.py(822)
├── BOA/         statement_template/{generate.py(694), editor/server.py(159), layout_*.json(12.8k行)}
├── 富国/        statement_template/{generate.py(592), editor/server.py(170), layout_static.json(3.3k行)}
├── 大通/        statement_template/{generate.py(17), generate_personal.py(1192), generate_business.py(2060),
│                editor_personal/server.py(147), editor_business/server.py(150)}
├── USbank/      statement_template/{generate_us.py(1203), generate_biz.py(1474),
│                editor_us/server.py(147), editor_biz/server.py(147)}
├── 水星C1sofi/  {mercury,c1,sofi}-statement-template/*.py + statement-editor/{editor_server.py(521), editor.html(794)}
├── alliant/     纯静态前端（pdf-lib 浏览器内渲染）
├── ally bank/   纯静态前端
└── scripts/     assets.py, lfs_assets.py, seed_examples.py, extract_private.py, private-assets.json
```

目录名混排四种命名法：`AE`（缩写）、`BOA`（全大写缩写）、`USbank`（驼峰）、`水星C1sofi`（中文+拼接）、`富国`/`大通`（纯中文）、`ally bank`（带空格）。带空格和中文的路径在 CI、Windows、Docker volume 上都是持续摩擦源。

### 1.3 三种互不兼容的引擎范式

| 范式 | 模板 | 入口签名 | 状态 |
|---|---|---|---|
| 从零绘制 | BOA、富国、AE、Mercury、C1、SoFi | `build_pdf(d, out, watermark=True)` / `build_pdf(d, out, watermark, mark, fonts)` / `build_pdf(data, dataset_label, with_watermark, metadata, xmp)` / `build(meta=None)` | 3 种写法 + 4 处模块级全局 |
| 底稿重放 | 大通(×2)、USbank(×2) | `build_pdf(d, out_path, watermark=True)`，内部 `_open_prepared(tag)` 打开预烘焙 PDF → 按 `layout_*.json` 的 span 表 `replace_span` 原位替换 | 依赖 `assets/*_prepared.pdf` + `prepared_*.json` 签名缓存 |
| 纯前端 | alliant、ally | 无后端，pdf-lib/fontkit 在浏览器内组装 | 与其余 11 个完全不同的技术栈 |

`build_pdf` 这个名字撒了三次谎：AE 的 `build_pdf` 返回 `fitz.Document` 不落盘；大通的 17 行 `generate.py` 只是分发器；Mercury/C1/SoFi 的 `build()` 什么都不接收，数据靠编辑器 `m.DATA = data` 注入。

### 1.4 实测基线（重构的 parity 判据）

`baseline_probe.py` 在子进程里逐个渲染，取文本层 SHA-256 作为版式指纹：

| 模板 | 页数 | 字节 | 渲染耗时 | text_sha256_16 | 备注 |
|---|---|---|---|---|---|
| wf | 5 | 95,146 | 0.29s | 74863165fbcfe116 | |
| ae | 2 | 81,402 | 0.13s | 1dc225d6690dec62 | |
| merc | 2 | 212,226 | 0.31s | 9b6f247efb8b9176 | ⚠ `2 extra bytes in post.stringData array` |
| c1 | 3 | **10,624,694** | 0.93s | 86a4a8cba5a2616b | 嵌了 Hiragino Sans GB W3 全字重 |
| sofi | 2 | 527,935 | 0.33s | 061916139197d514 | |
| boa-personal | 4 | 297,185 | 0.41s | dede8ceba43c6dec | |
| boa-business | 5 | 1,023,224 | 0.52s | 7dba208fe5105aec | |
| chase-personal | 2 | 210,765 | 0.12s | 07dbe4e74d7fdcc0 | 首次需烘焙底稿（冷启 4.65s） |
| chase-business | 2 | 210,011 | 0.12s | ca4d816b5b7dccba | |
| us-personal | 3 | 219,291 | 0.15s | 134e12b551c84109 | |
| us-business | 2 | 218,332 | 0.18s | 998fc8e80f828679 | |
| alliant | — | 2,161,610 | — | — | 32 文件纯静态 |
| ally | — | 10,644,720 | — | — | 13 文件纯静态（字体全量内联） |

两次连续运行文本层哈希完全一致（字节数会因 PDF 内嵌随机 ID 浮动 1–2 字节）→ **引擎文本层是确定性的**。因此 parity 判据只看 `pages / size / text_sha256_16 / text_chars / fonts`，`bytes` 仅作提示。

### 1.5 结构性问题清单

**P-1 六份编辑器服务互为副本，能力却在漂移**

| 端点 | 富国 | BOA | 大通×2 | US×2 | Mercury 组 |
|---|---|---|---|---|---|
| `/api/data` | ✅ | ✅ | ✅ | ✅ | `/api/state?tpl=` |
| `POST /api/generate` | ✅ | ✅ | ✅ | ✅ | `POST /api/render` |
| `/api/pages`（页哈希列表） | ✅ | ❌ | ❌ | ❌ | 合并进 render |
| `/preview.pdf` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `/export/statement.pdf?wm=&enc=` | ✅ | ✅（无参数） | ✅（无参数） | ✅ | `POST /api/export` |
| `/export/data.json` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `/api/reset` | ✅（复制 `data_sample.json`） | ✅ | ✅ | ✅（依赖 `.bak`） | ❌ |
| `/pv/{hash}.png` | ✅ | ✅ | ✅ | ✅（按页号，非哈希） | ✅ |
| `/api/coverage`（字形覆盖检查） | ❌ | ❌ | ❌ | ❌ | ❌（只有 AE 有） |

同一交付物，六份实现，能力取交集后只剩「读数据 → 生成 → 下载」。没有 `TemplateSpec`，没有共享路由，改一个下载头的 bug 要改六处。

重复度实测：USbank `editor_us` ↔ `editor_biz` 服务端 **92%**、前端 **98%**；大通 `editor_personal` ↔ `editor_business` 服务端 **88%**；BOA ↔ USbank 服务端 59%；`generate_us.py` ↔ `generate_biz.py` **81%**（1203 / 1474 行）。

**P-2 模块级全局让引擎不可并发、不可测试**

- 富国 `editor/server.py`：`STATE = {"watermark": True, "mark": True, "fonts": "declared"}`
- AE `editor.py`：`STATE = {"pdf": None, "path": None}`；引擎 `global DATA, doc`
- 大通/US 各 server：`STATE` 持水印开关
- Mercury/C1/SoFi：`m.DATA = data`、`m.WATERMARK = bool(...)`、`m.META = meta`

后果：同一进程内两个请求必然互相污染（靠 `LOCK` 串行化掩盖）；水印开关甚至被写回数据文件（`data["watermark"] = watermark` 之后 `json.dump`），于是「数据」里混进了「输出选项」。

**P-3 同名模块强制进程隔离**

`generate.py` 在 BOA / 大通（分发器）各一份；`chstream.py`、`barcode.py` 在大通与 USbank 各一份。所有引擎都是 `sys.path.insert(0, BASE) + import generate`，同一进程第二次 import 命中 `sys.modules` 拿到上一个模板的模块。
→ 这是 `run.py` 必须起 **8 个子进程**、再在前面挂一个 **8800 端口反代网关**、还要**嗅探 `Referer` 决定归属**的根本原因。为了绕开命名空间冲突，付出了整套进程编排的成本。

**P-4 数据契约 13 套，字段名冲突**

- 期间：`period.begin/end`（BOA）vs `period.start`（大通/US）
- 交易：`deposits[]/withdrawals[]`（US）vs `transactions[{deposit,withdrawal,balance}]`（富国）vs `transactions[{amount}]`（AE）
- 金额：格式化字符串（`"1,234.56"`）与整数分混用
- 摘要：`summary{begin,deposits,withdrawals,ending}`（US）只有它有；其余模板各自推导

**P-5 派生产物入库**

`assets/chase_static_{personal,business}_prepared.pdf`、`font_tables_*_prepared.json`、`prepared_*.json`（签名缓存）、`pv_png/`、`replay_*.json`、`baked-lines.js` 全部提交进 Git 且走 LFS。它们是「字体扩展 + F3 补字 + Type3 归一」的烘焙结果，与引擎版本强耦合；`_prep_sig()` 只哈希源 PDF 的 mtime/size 加 `b"prepared-v1"`，引擎逻辑改了而签名不变 → 静默使用过期底稿。

**P-6 资源管线带着历史包袱**

`scripts/private-assets.json`（125 条，名字带 private，实际是公共字体/底稿）、`extract_private.py`（从旧版源码抽取字面量资产）、`.gitattributes` 把 pdf/png/ttf 全推 LFS（非 LFS 环境拿到 86 个指针文件）、`scripts/assets.py check` 报“89 个未就绪”。

**P-7 路径耦合**

`data_path("USbank/statement_template/editor_us/data_us.json")` 这种「逻辑名 = 物理路径」的调用 20+ 处。目录一改名，全线崩。

**P-8 质量门缺失**

无 `pyproject.toml`、无 lint/format/type 配置、无 CI；测试 26 个用例只覆盖 `assets`/`lfs_assets`/`seed_examples`/`extract_private`/网关（1.27s 全绿），**PDF 生成与编辑器 API 零覆盖**——0.35s 就能改坏版式而测试全绿。

**P-9 顺带发现的真实缺陷（重构时一并修）**

1. `c1` 产出 10.6MB：子集化失效，嵌入了 `Hiragino Sans GB W3` 整套字重。
2. `merc` 产出结构畸形：stderr `2 extra bytes in post.stringData array`（手工构造 post 表长度算错）。
3. `chase` 冷启 4.65s（烘焙底稿）、热启 0.12s —— 烘焙产物却入库且签名机制不覆盖引擎版本。
4. `ally` 10.6MB 静态资源（字体全量内联，同 c1 病灶）。
5. `/api/generate` 出错也返回 HTTP 200 + `{"ok":false}`，无法用状态码做健康检查。

---

## 2. 目标架构

### 2.1 设计原则

1. **一个模板 = 一个 `TemplateSpec` + 一个 `Renderer` 实例**，不允许模块级可变状态。
2. **数据与选项分离**：`StatementData`（业务数据）与 `RenderOptions`（水印/加密/字体策略）是两个对象，永不互相写入。
3. **命名空间消灭同名**：`billgen.themes.chase.engine` 与 `billgen.themes.usbank.engine` 天然不冲突 → 单进程可并存 → 删掉 8 个子进程和反代网关。
4. **派生不入库**：烘焙底稿、预览 PNG、签名缓存一律进用户缓存目录（XDG），由内容签名+引擎版本失效。
5. **契约先行**：数据模型与 HTTP 端点先写 schema/契约测试，再改实现。
6. **parity 是硬门槛**：任何阶段结束必须 `baseline_probe --compare` PASS。

### 2.2 目录结构

```
bill-gen/
├── pyproject.toml                  # hatchling + ruff + mypy + pytest + pre-commit
├── README.md  CHANGELOG.md  LICENSE  .pre-commit-config.yaml
├── .github/workflows/ci.yml        # lint / type / test / parity（macos+ubuntu, py3.11-3.13）
├── src/billgen/
│   ├── cli.py                      # billgen serve|render|assets|parity
│   ├── spec.py                     # TemplateSpec / RenderOptions / RenderResult
│   ├── registry.py                 # 唯一模板注册表
│   ├── models/
│   │   ├── statement.py            # StatementData（pydantic v2）
│   │   ├── money.py                # Money（整数分，渲染时格式化）
│   │   └── legacy.py               # 旧 JSON → StatementData 适配（13 个 from_*_legacy）
│   ├── engine/
│   │   ├── base.py                 # Renderer 协议 + RenderContext
│   │   ├── pdf.py                  # 字体/水印/加密/页哈希等公共能力
│   │   ├── fonts.py                # 字体表、子集化、Type3/CMap 工具
│   │   └── prepared.py             # 底稿烘焙缓存（XDG + 签名）
│   ├── themes/
│   │   ├── boa/{engine.py, layout/personal.json, layout/business.json, assets/, default.json}
│   │   ├── wellsfargo/{engine.py, layout.json, assets/, default.json}
│   │   ├── amex/{engine.py, assets/, default.json}
│   │   ├── chase/{engine.py, personal.py, business.py, layout/, assets/}
│   │   ├── usbank/{engine.py, personal.py, business.py, layout/, assets/}
│   │   ├── mercury/{engine.py, ...}
│   │   ├── capitalone/{engine.py, ...}
│   │   ├── sofi/{engine.py, ...}
│   │   └── web/{alliant, ally}/    # 纯前端模板：仅静态资源 + spec
│   ├── server/
│   │   ├── app.py                  # 组装 FastAPI，注入 registry
│   │   ├── deps.py                 # 依赖注入：registry / settings / cache
│   │   ├── routes/{catalog,render,export,preview,assets,legacy}.py
│   │   └── static/                 # web/ 构建产物（或直接托管 web/dist）
│   ├── assets/{manifest.json, sync.py, seed.py}
│   └── parity/{baseline.py, baseline.json}
├── web/                            # 前端源码：单页 + 由 spec 驱动的表单
│   ├── index.html  src/{main.ts, form.ts, api.ts, preview.ts}
│   └── vite.config.ts
├── tests/
│   ├── test_parity_golden.py       # 13 入口 × golden 快照
│   ├── test_api_contract.py        # 端点契约（含 legacy 别名）
│   ├── test_models.py              # StatementData 校验 + legacy 适配
│   ├── test_engine_units.py        # 金额/分页/字体覆盖
│   └── test_assets.py              # 原 26 个用例迁移
└── docs/{ARCHITECTURE,DATA-SCHEMA,MIGRATION,CONTRIBUTING}.md
```

### 2.3 核心契约（骨架代码）

**`src/billgen/spec.py`** —— 唯一的模板描述，取代 6 份 `TEMPLATES` dict：

```python
from dataclasses import dataclass, field
from typing import Literal

TemplateKind = Literal["from-scratch", "replay", "static-web"]

@dataclass(frozen=True, slots=True)
class RenderOptions:
    watermark: bool = True
    encrypt: bool = True
    font_policy: Literal["declared", "subset", "embed-all"] = "subset"
    page_dpi: int = 110

@dataclass(frozen=True, slots=True)
class RenderResult:
    pages: int
    pdf: bytes
    hashes: tuple[str, ...]        # 每页预览 PNG 的内容哈希
    elapsed_ms: int

@dataclass(frozen=True, slots=True)
class TemplateSpec:
    id: str                        # "chase-personal"
    display_name: str
    kind: TemplateKind
    engine: str                    # "billgen.themes.chase.engine:PersonalRenderer"
    default_data: str              # 包内相对路径
    variants: tuple[str, ...] = () # 同一引擎的 tpl 取值
    supports: frozenset[str] = field(default_factory=frozenset)  # {"watermark","encrypt","preview"}
    legacy_prefixes: tuple[str, ...] = ()   # ("/boa", "/chase/personal")
    output_name: str = "statement.pdf"
```

**`src/billgen/engine/base.py`** —— 统一 5 种入口签名：

```python
from typing import Protocol

class Renderer(Protocol):
    spec_id: str
    def render(self, data: "StatementData", opts: RenderOptions) -> RenderResult: ...
    def preview_png(self, page: int, opts: RenderOptions, dpi: int) -> bytes: ...
```

实现侧的形态（以一个模板为例）：

```python
class ChasePersonalRenderer:
    spec_id = "chase-personal"

    def __init__(self, assets: Path, cache: PreparedCache) -> None:
        self._layout = json.loads((assets / "layout_personal.json").read_text())
        self._tables = json.loads((assets / "font_tables_personal.json").read_text())
        self._cache = cache                      # 取代模块级 _open_prepared 全局

    def render(self, data, opts):
        doc = self._cache.open_prepared("chase-personal", self._tables)   # 命中/烘焙
        ...                                       # span 替换逻辑原样搬过来
        return RenderResult(pages=doc.page_count, pdf=doc.tobytes(...), hashes=...)
```

全局态消灭对照表：

| 现在 | 之后 |
|---|---|
| `STATE = {"watermark": ...}`（6 处） | `RenderOptions.watermark` 形参 |
| `m.DATA = data` / `m.WATERMARK = ...` | `Renderer.render(data, opts)` |
| `global DATA, doc`（AE） | `RenderContext` 局部变量 |
| `CH2CODE` / `WIDTHS` 模块级 dict 被烘焙结果就地修改 | `FontTable` 不可变对象，缓存返回副本 |
| `data["watermark"] = watermark` 写回数据 | 选项与数据彻底分离 |

**`src/billgen/registry.py`**：

```python
REGISTRY: dict[str, TemplateSpec] = {s.id: s for s in (
    TemplateSpec("boa-personal", "BOA Personal", "from-scratch",
                 "billgen.themes.boa.engine:Renderer", "default.json",
                 variants=("personal",), legacy_prefixes=("/boa",)),
    TemplateSpec("chase-personal", "Chase Personal", "replay",
                 "billgen.themes.chase.engine:PersonalRenderer", "default.json",
                 variants=("personal",), legacy_prefixes=("/chase/personal",)),
    # … 13 条
)}

def load_renderer(spec_id: str) -> Renderer:
    target = REGISTRY[spec_id].engine
    mod, _, attr = target.partition(":")
    return getattr(importlib.import_module(mod), attr)(assets_dir(spec_id), CACHE)
```

**`src/billgen/models/statement.py`** —— 一份数据模型，13 个 legacy 适配器喂它：

```python
class Period(BaseModel):
    start: date
    end: date

class Txn(BaseModel):
    date: str
    description: str
    amount: Money              # 整数分，渲染时才格式化
    kind: Literal["deposit", "withdrawal", "fee", "interest"] = "withdrawal"
    detail: str | None = None

class StatementData(BaseModel):
    template: str
    period: Period
    account: Account           # number_display / name / micr
    customer: Customer         # lines / zip
    transactions: list[Txn]
    summary: Summary | None    # 缺省则由 transactions 推导
    bank: BankProfile | None
    extra: dict[str, Any] = {}
```

`models/legacy.py`：

```python
def from_boa_legacy(raw: dict) -> StatementData: ...      # period.begin/end + deposits[]
def from_chase_legacy(raw: dict) -> StatementData: ...    # period.start + micr{}
def from_us_legacy(raw: dict) -> StatementData: ...       # deposits[]/withdrawals[] + summary{}
def from_wf_legacy(raw: dict) -> StatementData: ...       # transactions[{deposit,withdrawal}]
```

适配器保证「老 JSON 照样能渲染」；新前端只产出 `StatementData`。`/export/data.json` 默认导新格式，`?format=legacy` 导旧格式（保持向后兼容）。

**`src/billgen/server/routes/legacy.py`** —— 显式承载「功能一致」：

```python
LEGACY_ROUTES = {
    "/api/data":        ("GET",  "catalog.read_data"),
    "/api/generate":    ("POST", "render.generate"),
    "/api/pages":       ("GET",  "preview.pages"),
    "/preview.pdf":     ("GET",  "preview.pdf"),
    "/export/statement.pdf": ("GET", "export.pdf"),   # 接受 wm=0&enc=1
    "/export/data.json":     ("GET", "export.data"),
    "/api/reset":       ("POST", "catalog.reset"),
    "/pv/{name}.png":   ("GET",  "preview.png"),
}
```

老的 `/boa/`、`/chase/personal/` 网关前缀用 302 到 `/t/{spec_id}/`；`Referer` 嗅探逻辑随子进程一起删除。

### 2.4 关键选型（ADR 摘要）

**ADR-1 服务端框架：FastAPI + uvicorn（推荐）**，理由：`TestClient` 让 6 个复制粘贴的服务第一次拥有契约测试；`Depends` 天然替代模块全局；OpenAPI 自动成为前端与外部脚本的接口文档。成本：两个新依赖。
*保守后路*：若坚持零框架，写一个 120 行 `Router`（正则 path → handler + 统一错误包装）替换 6 份 `do_GET/do_POST`，收益是 diff 更小，但测试体验和文档能力仍缺。二选一，不要继续复制粘贴。

**ADR-2 数据校验：pydantic v2**。13 套异构 JSON 需要一个统一入口做归一与报错定位。若嫌重，可用 `dataclass + 手写 validate()`，但 `models/legacy.py` 那层必须存在。

**ADR-3 前端：单个 Vite 应用 + spec 驱动表单**。现在 5 份 `index.html` 重复 44%–98%，字段写死在 HTML 里；改成 `/api/catalog/{spec_id}` 返回字段 schema，前端按 `type/format/required` 动态生成输入，新增模板零前端改动。纯静态的 alliant/ally 作为 `static-web` 类型保留原样（它们本来就是浏览器内渲染，没有后端可收敛），但资源同样过一遍字体子集化（10.6MB → 目标 <2MB）。

**ADR-4 缓存目录**：`BILLGEN_CACHE_DIR`（默认 `$XDG_CACHE_HOME/billgen`）。签名改为 `sha256(源资产内容 + 字体表 + ENGINE_VERSION)`，`ENGINE_VERSION` 是包内常量，引擎改动必须 bump —— 修掉「改了逻辑还在用旧底稿」。

---

## 3. 迁移路线（每阶段独立可发布）

### P0 冻结现状（1 天）
- 把 `baseline_probe.py` 与 `baseline.json` 提交进新仓 `src/billgen/parity/`
- 记录 26 个既有单测通过、13 入口哈希、两个已知缺陷（c1 体积 / merc post 警告）为 `known_issues`
- 在旧仓根加 `docs/ARCHITECTURE.md` 描述现状，作为重构前的唯一事实来源

**验收**：`billgen parity record` 产出快照；`--compare` 自比对 PASS。

### P1 立骨架，不动业务（2–3 天）
- `pyproject.toml`（hatchling，`src/` 布局，ruff+mypy+pytest 配置，`billgen` console script）
- 目录重命名：`AE→themes/amex`、`BOA→themes/boa`、`富国→themes/wellsfargo`、`大通→themes/chase`、`USbank→themes/usbank`、`水星C1sofi/{mercury,c1,sofi}→themes/{mercury,capitalone,sofi}`、`alliant|ally bank→themes/web/{alliant,ally}`
- `git mv` 保留历史；空格/中文路径一次性消灭
- `runtime_paths.py` → `paths.py`，`data_path()` 改为「逻辑名 → 包内路径」映射表

**验收**：`billgen render --template boa-personal` 能出 PDF 且哈希等于 baseline。此时引擎代码零改动，只换路径解析。

### P2 统一引擎接口（3–5 天）
- 定义 `Renderer`/`RenderOptions`/`RenderResult`，为每个模板写薄适配层（一个 `render()` 包住现有 `build_pdf`）
- 消除模块级全局：全局变量改为实例属性或形参
- 同名模块改包内相对导入（`from ..engine import pdf`），彻底解决 `generate` 冲突
- **删掉 `run.py` 的子进程编排与反代网关**，改为 `billgen serve` 单进程多模板

**验收**：13 个 `Renderer` 在**同一进程**内依次渲染全部 PASS 基线；`pytest -k parity` 全绿。

### P3 统一服务与数据层（4–6 天）
- FastAPI 应用 + 6 组路由 + `LEGACY_ROUTES` 别名表
- 13 个 legacy 适配器；`/export/data.json?format=legacy` 兼容
- 契约测试覆盖每个模板的 8 个端点（含 400/404/500 语义，顺手修“错误也返回 200”）
- 前端换单页应用，先接管 3 个模板（chase-personal / boa-personal / wf），其余留旧页面 302

**验收**：新老端点对同一数据返回字节级一致的 PDF（哈希比对）；`/api/catalog` 覆盖 13 条 spec。

### P4 清理与性能（3–4 天）
- 烘焙底稿/预览 PNG/签名缓存移出 Git（`git rm --cached` + `.gitignore`），首次运行生成到 XDG cache
- 字体子集化修复：c1 10.6MB → 目标 <500KB；ally 10.6MB → <2MB
- 修 merc `post.stringData` 警告
- `chase` 冷启从 4.65s 降到 <2s（烘焙并行 + 增量）
- `private-assets.json` → `assets/manifest.json`，`extract_private.py` 删除（历史抽取脚本，已无用）
- `.gitattributes` LFS 范围收敛到真正的大资产

**验收**：仓库体积 <8MB（不含 LFS 资源）；`billgen assets check` 零告警；三个缺陷关闭且基线哈希不变（体积/警告变化不影响文本层哈希）。

### P5 收口（2 天）
- CI：lint / type / test / parity（macOS + Ubuntu，py3.11–3.13）
- `docs/{ARCHITECTURE,DATA-SCHEMA,MIGRATION,CONTRIBUTING}.md`
- `pre-commit`（ruff、ruff-format、可选的 `check-yaml`）
- 版本号 + `CHANGELOG.md`；旧目录树在 P5 末尾删除

**验收**：空白克隆 + `pip install -e .` + `billgen assets sync` + `billgen serve` 一条命令可跑；CI 全绿。

---

## 4. 收益量化（可复核）

| 指标 | 现在 | 之后 |
|---|---|---|
| Python 文件数（引擎+服务） | 24 | 24（但 6 份服务 → 1 份 + 13 个薄适配） |
| 编辑器服务代码行 | ~1,240（6 份副本） | ~350（1 套路由 + 6 个路由模块） |
| 删除的重复行 | — | **≈1,800 行**（US 服务 92%×147、大通 88%×147、前端 98%×1 份） |
| 运行进程数 | 9（8 服务 + 网关） | 1 |
| 监听端口 | 9（8791–8798 + 8800） | 1 |
| 模块级可变全局 | 12+ 处 | 0 |
| 测试覆盖 | 26 用例，PDF/API 零覆盖 | golden parity 13×N + 端点契约 + 模型校验，目标行覆盖 ≥70% |
| 冷启（chase） | 4.65s（且靠入库产物） | <2s（XDG 缓存 + 版本签名） |
| c1 产物体积 | 10.6MB | <0.5MB |
| 静态资源 | 10.6MB（ally） | <2MB |
| 新增模板的成本 | 复制一个目录，改 6 处路径 + 1 份 server + 1 份前端 | 加一个 `themes/xxx/` + 1 条 `TemplateSpec` |

---

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 底稿重放模板对 span 表极敏感，目录一移动就找不到 assets | P1 只做 `git mv` + 路径映射，不改相对结构；每个主题的 `assets/` 保持在 `engine.py` 同级 |
| 字形/字体表在打包后失效（`font_path` 回退到 `/System/Arial`） | `FontRegistry` 显式列依赖字体，`billgen doctor` 检查缺失并给出安装提示；CI 用 Linux 的 Liberation 字体跑通 |
| legacy 端点语义（错误返回 200、`.bak` 重置）被外部脚本依赖 | `LEGACY_ROUTES` 保留原语义，新端点用标准状态码；文档标注 deprecated 与移除版本 |
| 前端重写引发布局回归 | 前端不改版式，只改数据出入口；预览仍走 `/pv/{hash}.png` 与像素无关 |
| QPDF/MuPDF 版本差异导致哈希漂移 | `requirements.txt` 锁 PyMuPDF 1.26.6；parity 只比文本层哈希与页数，不比字节 |

---

## 6. 立即可执行的第一步

```bash
# 1) 在新仓里冻结现状（parity oracle）
python3 -m venv .venv && .venv/bin/pip install -r bill-gen/requirements.txt
.venv/bin/python baseline_probe.py --repo bill-gen --python .venv/bin/python --out baseline.json

# 2) 建骨架
git init bill-gen-v2 && cd bill-gen-v2
#    pyproject.toml / src/billgen/{spec,registry,engine,server} / tests / docs

# 3) P1 的第一次搬迁（不动业务代码）
git mv ../bill-gen/BOA/statement_template src/billgen/themes/boa
git mv ../bill-gen/大通/statement_template src/billgen/themes/chase
...
.venv/bin/python -m billgen.parity.baseline --compare baseline.json   # 必须 PASS
```

---

## 附：为什么先做 parity 而不是先写 `pyproject.toml`

13 个模板的版式是**像素级**的（Chase/US 是「打开原稿 PDF、按 span 坐标原位替换」，坐标精确到 0.1pt；富国/Boa 是逐 bl/x 定位）。这类代码重构时最怕的不是结构不好看，而是**改完以后没人能证明版式没变**。所以第一步必须是把「现在的样子」变成机器可判定的哈希，之后每一步都可以放心大胆地动结构。

---

## P1 执行记录（已完成）

搬迁与统一接口阶段的实际结果：

| 项 | 结果 |
|---|---|
| 目录结构 | `src/billgen/` 布局，13 个模板迁入 `src/billgen/themes/`，旧中文/空格目录名全部消除 |
| 模板清单 | 6 份散落的 `TEMPLATES`/`DATA_FILES`/`SERVICES` → `registry.py` 单一注册表（13 条） |
| 入口签名 | 5 种历史签名 → `Renderer.render(data, opts) -> RenderResult`，由 `engine/adapters.py` 吸收 |
| 同名模块冲突 | `generate` / `chstream` / `barcode` 在三主题下同名 → `ThemeLoader` 按主题隔离 `sys.modules` |
| 单进程能力 | 11 个后端模板可在**同一进程**内顺序渲染（原本必须 8 个子进程 + 网关） |
| 等价性 | `billgen parity verify` → PASS（13 条指纹与搬迁前一致，仅 1 字节 PDF ID 浮动） |
| 测试 | 26 → 92（新增注册表契约、逐模板冒烟、确定性、水印开关、parity 稳定性） |
| 兼容 | `runtime_paths.py` 转发 + 旧逻辑路径自动翻译；单独执行模板脚本仍可用 |

已知遗留（P2–P5 处理）：模块级 `STATE`/`DATA`/`WATERMARK`/`META` 仍在；
Chase 与 US Bank 的双胞胎引擎未合并；Capital One 产物 10.6MB（Hiragino Sans GB 全字重）；
`run.py` 的多进程网关仍在仓库根。
