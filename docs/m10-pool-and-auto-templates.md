# M8：常驻连接与模板自动推荐

两个缺口：每出一张 PDF 就冷启一个 `billgen mcp` 子进程（解释器 + PyMuPDF 导入，几百毫秒），
以及模板要人手写进 `BILLGEN_TEMPLATES`。M8 把这两件事都补上，并把两端合成一个镜像部署。

## 一、常驻连接（`McpPool`）

`flow/packages/core/src/mcp/client.ts` 里的 `McpPool`：

- 以 `(command, args, env)` 为键复用子进程，进程级单例叫 `sharedMcpPool`；
- 调工具前先看子进程死活，死掉的直接丢弃重建；
- 调用中途子进程挂掉（`SERVER_EXIT` / `CLOSED` / `NOT_RUNNING` / `SPAWN_FAILED`）会**重建并重试一次**，
  其他错误原样抛出——工具自身的错误不该被重试掩盖；
- 空闲 `idleMs`（默认 10 分钟）回收，`dispose()` 全清。

`renderStep` 从 `withMcpClient`（一次一进程）改为 `sharedMcpPool.with(...)`，所以同一个 job 内
13 张 PDF、甚至连续多个 job，共用一个 Python 子进程。

实测：`packages/core/test/render.pool.test.ts` 用一个会说 JSON-RPC 的 stub 进程验证「两次调用同一个 pid」、
「杀掉后自动换新 pip」、「dispose 后不留孤儿进程」。

## 二、模板自动推荐（`recommend_templates`）

打分在 Python 侧（`src/billgen/synth/recommend.py`），不调模型、不渲染，全部基于可观察事实：

| 维度 | 权重 | 判据 |
|---|---|---|
| 描述长度 | 3.0 | 最长描述 ≤ 模板列宽（`desc_chars`）不扣分，超出按比例扣——截断在纸面上看得见 |
| 分页 | 2.0 | 整页 / 正常残页满分；`ceil(rows/per_page)` 页里最后一页只放 1 行 → 0.2 |
| 区块覆盖 | 1.0 | 账本需要支票 / ATM / 手续费 / 日终余额区块，模板有没有对应区块 |
| 稳定抖动 | 0.5 | `sha256(seed|template)[0]/255`：同 seed 可复算，不同 seed 换风格 |

**账户类型是硬门槛**：`binding.accepts(kind)` 不过的模板直接不进候选，不靠打分兜。

接法：

```
BILLGEN_TEMPLATES=auto        # 交给推荐器
BILLGEN_AUTO_LIMIT=2          # 每个账户保留前 N 名
BILLGEN_TEMPLATES=boa-personal,auto   # 混合：钉死一套 + 推荐器补一套
```

产物里带推荐依据，出片失败时不用猜为什么选它：

```json
{
  "template": "chase-personal",
  "pages": 2,
  "bytes": 201638,
  "score": 6.192,
  "reasons": [
    "accepts checking",
    "descriptions fit (39/44 chars)",
    "single page (9/30 rows)",
    "renders 3/3 sections this ledger needs"
  ]
}
```

进度流里多了一条 `progress` 事件（`推荐模板 checking: chase-personal, boa-personal`），
前端「进度」区块会显示。

### 顺手修掉的两个校验 bug（自动推荐把它们暴露了）

1. **必填项按脚手架算**：`contract.skeleton()` 会给 usbank/chase 之类补 `bank`、`promo`，
   但适配器草稿并不写这些键 → 自动模式一开就误报「缺少必需字段 bank」。
   改成用 `contract.required_keys()`（模板 `default_data` 的真实顶级键）。
2. **金额格式豁免漏了 usbank**：usbank 的正文本就用 `2,400.00` 这种千分位，判成「金额不能带 $ 或千分位」
   会误杀。`_FORMATTED_TEMPLATES` 补上 `usbank-personal` / `usbank-business`；另外 `bank.legal_na`（富国的叫法）
   也算法人名。

## 三、两个被自动推荐放大的假故障

自动模式一次会把 11 套版式都跑一遍，于是平时只跑固定模板碰不到的问题露头了：

- **透支被判成致命错**：`lint` 原来把「余额为负」当阻断项。真实账户本来就会透支，
  拦住它等于逼人去改数字——那才是造假账。现在这条降级为 `advisory`：
  `lint.verify()` 只拦阻断项，把提醒原样返回（`Issue.advisory=True`），PDF 照出。
- **续行拼太长**：usbank 的 ACH 续行原先把 `REF=<trace>Y00|PAYROLL` 拼成一条，
  在 Ref 列里折行，`PAYROLL` 被拆成两半。改成 trace 一行、`类型 + 公司号` 一行。

## 四、一个镜像，两个运行时

`Dockerfile` 两段构建：

1. `flow-build`（`node:22-bookworm-slim`）：`pnpm install --frozen-lockfile` → `pnpm --filter @bh/web build`。
2. `runtime`：同一 Node 基础镜像 + `python3`/`python3-venv` + `fonts-liberation2`/`fonts-noto-cjk`，
   billgen 装进 `/opt/billgen`（`pip install '.[mcp]'`），再拷 flow 整个工作区（含 `node_modules` 与 `dist`）。

`deploy/entrypoint.sh` 起两个进程并互相看护：任一退出就带走另一个，容器随之重启——
比「半个服务活着但健康检查还绿」诚实。`billgen serve` 在容器内绑 `0.0.0.0`
（端口映射仍只发布到宿主 `127.0.0.1`）：docker 的代理连不到只绑容器 loopback 的 socket。

```bash
docker compose -p billgen up -d --build --wait
# 前端 + LangGraph：http://127.0.0.1:8797
# billgen 工作台：  http://127.0.0.1:8800
```

flow 侧新增 `WEB_DIST`：设了就由 API 进程托管 `apps/web/dist`（SPA 回退到 `index.html`，
但 `/api/*` 永不回退——否则接口会被 HTML 顶掉，这条有测试钉住）。加 `GET /api/health` 供健康检查。

## 验证

```bash
# Python
.venv/bin/python -m pytest            # 494 passed, 16 skipped
.venv/bin/python -m ruff check src tests tools
.venv/bin/python -m mypy
.venv/bin/python tools/render_regression.py check

# flow
cd flow && pnpm -r test && pnpm typecheck
BILLGEN_MCP_CMD=$PWD/../.venv/bin/billgen pnpm --filter @bh/core test --testNamePattern 'over MCP'

# 容器（真实 LLM_FAKE 全链路）
docker compose -p billgen-test up -d --build --wait
curl -sS -X POST http://127.0.0.1:18797/api/generations \
  -H 'Content-Type: application/json' -H 'X-API-Key: devlocal' \
  -d '{"subjectType":"personal","accounts":{"checking":{"min":8,"max":13},"credit":{"min":20,"max":35}},"months":3,"endMonth":"2026-09","seed":"docker-auto-2"}'
```

## 已知边界

- 推荐器只认服务端可渲染的 11 套版式；`alliant` / `ally-bank` 是浏览器端模板，不参与打分。
- `auto` 的候选质量取决于账本本身：没有支票、没有 ATM 的账本会把「区块覆盖」这一项压平，
  真正的区分度只剩描述长度与分页。
- 池子是进程内的：`tsx watch` 热重载会重建池，冷启动成本跟着回来（生产用 `start` 不受影响）。
