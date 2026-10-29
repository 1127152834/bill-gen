# M7：LangGraph → MCP → billgen

一句话：**图生成账本，模型填表单，Python 出片，PDF 路径回写结果包。**

```
┌──────────────────────────── LangGraph（flow/，Node） ────────────────────────────┐
│ normalize → planResearch → research×N → lifeModel → schedule → descriptors        │
│           → validate ⇄（R1–R11 不合格回灌）→ audit → finalize                     │
│           → billgen（本里程碑）                                                    │
└───────────────────────────────────┬──────────────────────────────────────────────┘
                                    │ MCP over stdio（JSON-RPC，换行分帧）
                                    ▼
┌──────────────────────────── billgen（Python，PyMuPDF） ──────────────────────────┐
│ list_templates / template_contract / draft_from_ledger / validate / render / preview │
│ 版式 13 套 · 品牌替换 · 自检（金额闭合、真实行名残留、SAMPLE 标记）                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## 为什么这样切

| 关注点 | 归属 | 理由 |
|---|---|---|
| 人设、调研、金额分布、余额连续性 | `flow/`（TS） | 有 LLM/LangGraph/搜索，JS 生态 |
| 版式、字体、坐标、品牌 | `billgen`（Python） | PyMuPDF 与像素级复刻，13 套模板已等价验证 |
| 两者之间 | **MCP 工具调用** | 进程边界即 JSON；Python 不需要联网，Node 不需要碰 PDF |

渲染节点是**附加的**：`deps.billgen` 不存在时图与旧版逐字节相同（事件流也一样），所以离线 fixture 路径和 386 条图的测试不受影响。

## 开启方式

```bash
# flow/.env（或环境变量）
BILLGEN_MCP_CMD=../.venv/bin/billgen     # 留空 = 只生成账本，不出 PDF
BILLGEN_TEMPLATES=boa-personal,chase-personal
BILLGEN_OUT=./data/renders
BILLGEN_AI_FILL=1                        # 0 = 直接渲染确定性草稿（fixture 模式自动为 0）
```

```bash
cd flow && pnpm --filter @bh/server start
curl -s -X POST http://127.0.0.1:8797/api/generations \
  -H 'Content-Type: application/json' -H "X-API-Key: $API_KEY" \
  -d '{"subjectType":"personal","accounts":{"checking":{"min":8,"max":13}},"months":3,"endMonth":"2026-09"}'
```

结果包里多出 `renders[]`：

```json
{
  "template": "boa-personal",
  "account": "checking",
  "path": "/repo/flow/apps/server/data/renders/boa-personal-checking-2a72706e.pdf",
  "pages": 4, "bytes": 180537,
  "sha256": "5abd6ac6…",
  "attempts": 1,
  "modelFilled": false
}
```

`attempts` 是填表轮数，`modelFilled` 说明模型是否真的改过数据。

## 填表是怎么教会模型的

模型不猜字段名，四样东西一起给它（`src/billgen/mcp/contract.py`）：

1. **骨架**：模板的完整默认数据（含 `watermark` / `metadata` / `micr` / `promo` 等 20–40 个键）。
2. **类型表**：`paths → "string" | "number-as-string" | "date-string" | "list[...]"`。
3. **字段说明**：约 30 条通用 + 每个模板自己的补充（`account_display` 打码位、`daily` 只写有变动的日期、`checks[].paid` 与 `date` 的区别……）。
4. **硬规则**：8 条 —— 金额不带 `$`/千分位、期初 + Σ贷 − Σ借 = 期末、日期 `01/04/27` 或 `Jan 4` 按模板、描述不超过 38（模板各异）、行名必须虚构、`metadata.title` 保留 `SAMPLE — NOT A REAL BANK STATEMENT`、`auto: true` 时不要手填 `summary`。

再加一份**确定性草稿**：`draft_from_ledger` 用 `billgen.synth` 把 flow 的 `Statement` 转成该模板的数据（含金额格式化、分区、日终余额、支票号、流水号）。模型的任务因此从「从零构造」降级成「修改一份已闭合的草稿」，这才是它能稳定做对的部分。

## 填表循环

```
draft = draft_from_ledger(...)          # 基线，永远不被覆盖
for attempt in 1..3:
    answer = model(base=draft, errors=last_errors)   # 每轮都从基线重来
    if validate(answer).ok: break
    last_errors = validate(answer).errors
render(template, answer)                # 仍不合法就记 warning，不中断任务
```

两个设计取舍：

- **重试以基线草稿为起点**，不是以上一轮的坏答案。否则模型会把 `begin_balance: "$1,000.00"` 一路抄下去 —— 这是实现时踩到的坑，`test/render.mcp.test.ts` 里有一条用例专门钉住它。
- **校验失败只降级成 warning**（`renders` 里少一张、`result.warnings` 多一条），不把整个 job 判失败。图生成的数据本身是好的，只是某个模板没吃下去。

## 工具清单

| 工具 | 作用 | 参数要点 |
|---|---|---|
| `list_templates` | 11 个可渲染模板 + 支持的账户类型 | — |
| `template_contract` | 骨架 / 类型 / 说明 / 规则 / 字数与行数上限 | `template_id` |
| `draft_from_ledger` | 由 flow 结果包生成该模板的闭合草稿 | `result`、`account`、`cycle`（-1 = 最后一期）、`bank_name` |
| `validate` | 真渲染一次 + 金额检查 + 真实行名残留 + SAMPLE 标记 | `data` |
| `render` | 写盘并返回 page/bytes/sha256/brand_leftovers | `filename`、`watermark`、`encrypt` |
| `preview` | 首页 PNG | `page`、`dpi` |

`credit` 账户没有版式（11 套服务端模板都是存款账户版式），所以渲染节点只挑 `checking` / `business_checking`。

## 客户端

`flow/packages/core/src/mcp/client.ts` 是自带的 stdio JSON-RPC 客户端（`initialize` / `tools/list` / `tools/call`），不引 `@modelcontextprotocol/sdk`：

- 子进程失败（二进制不存在）会 reject 所有 pending 请求，节点转 warning —— `test` 里有一条用不存在的路径钉住这条。
- 超时 180s（出片要几秒，冷启动更久）。
- `structuredContent` 优先，退化到 `content[0].text` 的 JSON。

## 验证

```bash
# 需要先装 MCP 依赖：pip install -e '.[mcp]'
cd flow && BILLGEN_MCP_CMD=../.venv/bin/billgen pnpm test    # 386 + 72 + 78 passed
BILLGEN_MCP_CMD=../.venv/bin/billgen pnpm --filter @bh/core exec vitest run test/render.mcp.test.ts
```

`test/render.mcp.test.ts`（5 条，全部离线、真起 Python 子进程）：

1. 每条 checking 语句都出片，`%PDF-` 头、页数、字节数、落盘路径。
2. **模型改的数据真的到了 PDF 文本层**：桩模型把 `customer_lines[0]` 改成 `SCENARIO FOOTER LINE`，用 PyMuPDF 抽文字断言命中。
3. **校验失败会回灌**：第一轮故意写 `$1,000.00`，第二轮修复，`attempts === 2` 且事件流里有 `failed validation`。
4. 服务起不来时只记 warning、不抛异常。
5. `tools/list` 恰好六个工具。

## 已知边界

- `BILLGEN_TEMPLATES` 要手写；没有「模型自己挑模板」这一步（模板选择是业务决定，不该让模型随机）。
- `alliant` / `ally-bank` 是纯浏览器端模板，不在 MCP 清单里。
- 每次渲染起一个新子进程（`withMcpClient`）；批量出片时值得改成常驻连接 + 会话复用以省冷启动。
- 模型的 `render` 调用会占用预算（`budget.chargeLlm()`），`LLM_FAKE=1` 下不触发（`modelFill` 强制为 0）。
