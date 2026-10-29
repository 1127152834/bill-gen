# M5 · MCP 服务：让 Agent 自己填表、自己出片

M4 之前，账单是「Python 拿到账本 → 适配器拼数据 → 引擎出片」。这一步把**填表**交出去：
把 billgen 的渲染能力做成 MCP 工具，让 LangGraph 里的模型自己读契约、自己填字段、自己看错误改。

```
LLM(float)  ──tools/list──▶  billgen-mcp
     │                          │  list_templates       11 个模板
     │                          │  template_contract    骨架 + 类型 + 字段说明 + 规则 + 限额
     │                          │  validate             只检查不落盘
     │                          │  render               出 PDF，回路径/页数/sha256
     │                          │  preview              出 PNG，给模型看
     │                          │  draft_from_ledger    flow 结果 → 数据草稿
     └────── structured JSON ◀──┘
```

## 1. 为什么是「契约」而不是「JSON Schema」

模板数据是**异构**的（见 [m1-synth.md](m1-synth.md)）：金额一会是 `"1000.00"`、一会是 `"$1,000.00"`，
期号字段叫 `period.begin` / `period.start` / `statement_date` 三种。只给模型一个 JSON Schema，
它会填出「结构合法但引擎读不懂」的数据。

`template_contract` 给的是四样东西：

| 键 | 内容 | 解决什么 |
| --- | --- | --- |
| `data` | 该模板**能直接出片**的完整默认数据（深拷贝） | 模型不需要从零编 JSON，改哪填哪 |
| `types` | 路径 → 类型（`money` / `date:MM/DD/YY` / `list[...]` / `bool`） | 一眼看出这里要字符串还是数字 |
| `notes` | 路径 → 一句话人话（37 条通用 + 每模板补充） | 「这个字段到底是什么」 |
| `rules` / `limits` | 8 条硬规则 + 每模板的 `desc_chars`/`rows_per_page` | 闭合、日期、虚构行名、SAMPLE 水印、描述截断 |

外加一层**脚手架**（`contract.py::_scaffold`）：默认数据里没有、但合成场景必须填的块先补占位。
目前补两类——

- BOA 的 `promo`（`Balance Connect™`、`Business Fundamentals®`、`SmartAsset`、`Merrill` 的替身），
  不给就会印出真实商品名；
- `chase` 的 `product`（正文里会引用），不给就会印 `CHASE COLLEGE CHECKING`；
- `amex`/`usbank`/`mercury`/`capitalone`/`sofi` 的 `bank` 块（引擎读它但当默认数据里没有），
  不给就印原版行名。

这层脚手架是踩出来的：11 个模板里有 5 个的默认数据**没有** `bank`，模型照着填也只会填出 `Sample Bank`。

## 2. 校验做了什么

`validate` 是「改稿循环」的核心，它同时做四件事：

1. **必填**：拿契约骨架的顶层键逐项比对（不是写死 `period`——`wellsfargo` 用 `statement_date`）。
2. **金额格式**：扫 `amount`/`deposit`/`withdrawal`/`balance`/`begin_balance` 里的 `$` 与千分位；
   `wellsfargo` 的展示列本来就是格式化串，整表豁免；`summary`/`totals`/`daily` 子树也豁免。
3. **真渲染一次**：能出片才算过，`KeyError` 之类一律变成可读错误而不是让工具崩。
4. **文本层审计**：出片后扫真实银行标识（行名/域名/行址/电话/商品名），未登记的残留算 error。
   这条最有用——模型改完行名以为干净了，`{'USbank': 3}` 会当场告诉它没干净。

## 3. 渲染链路自带品牌替换

`render`/`validate`/`preview` 出片后都会过一遍 `branding.apply`：logo 物理替换、行址/客服电话/域名
按 `bank.name` 派生（`branding.brand_from_data`）。所以模型只要把 `bank` 填对，版式里的真实行标
不会漏出来。

## 4. 用法

```bash
# 装可选依赖
.venv/bin/pip install -e '.[mcp]'

# stdio 起服务（MCP 客户端会自己拉起，一般不必手动跑）
.venv/bin/billgen mcp --out /tmp/billgen-mcp

# 或者
.venv/bin/python -m billgen.mcp
```

LangGraph 侧接法（下一步）：

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(command=".venv/bin/billgen", args=["mcp"])
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        contract = await session.call_tool("template_contract", {"template_id": "boa-personal"})
        ...
```

## 5. 验证

```bash
.venv/bin/python -m pytest tests/test_mcp.py -q     # 19 passed
```

测试覆盖：11 个模板契约齐备、脚手架补齐（`promo`/`product`/`bank`）、每个模板「换虚构行名后
默认骨架能直接出片」、裸金额拒绝、真实行名拒绝、`render` 落盘 + `%PDF-` 头 + 无残留、
`preview` 出 PNG、`draft_from_ledger` 由 flow 结果生成草稿。

## 6. 已知边界

- `alliant` / `ally-bank` 是纯浏览器端模板（`kind=static-web`），不在 MCP 清单里。
- `mcp` 是可选依赖（`pip install -e '.[mcp]'`），核心渲染不依赖它。
- 现在 MCP 只暴露渲染；**账本怎么造**仍在 flow 的 LangGraph 里，两边在
  `GenerationResult` JSON 上会合。

## 下一步

图侧怎么用这套工具（拉契约、模型填表、校验回灌、出片）见 [M7：LangGraph → MCP](m7-langgraph-mcp.md)。
