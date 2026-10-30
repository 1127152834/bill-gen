# M10：单前端入口 + 前端可配模型

在此之前，一个仓库里跑着两个前端、两个端口：`8800` 是渲染工作台，`8797` 是账单猎手。
M10 把猎手折进同一个入口，并把模型配置从环境变量搬到页面里。

## 拓扑

```
浏览器 ──► :8800 ─┬─ /                     工作台（13 模板）
                  ├─ /assets, /favicon.svg 猎手前端产物
                  ├─ /new /history /g/:id   猎手页面路由
                  ├─ /api/v1/...            渲染 API（含 synth）
                  ├─ /api/v1/hunter/*       猎手状态 / 配置 / 重启
                  ├─ /api/generations*      ┐
                  ├─ /api/health            ├─反代到 127.0.0.1:8797
                  └─ /hunter/*              ┘
```

猎手不再是独立容器进程的搭档，而是 `billgen serve` 的子进程：

- `src/billgen/hunter/process.py` 用 `subprocess` 拉起 `node_modules/.bin/tsx apps/server/src/index.ts`，
  独立进程组、日志环形缓冲（状态接口返回最后 40 行）、`SIGTERM`→`SIGKILL` 收尾；
- `BILLGEN_FLOW_ROOT` 指目录，缺目录/缺 `node_modules`/缺 `node` 时 `enabled=false` 并给出中文原因，
  容器外只跑工作台也不会报错；
- `POST /api/v1/hunter/restart` 供「重启服务」按钮用。

## 前端

顶栏分段控件切换 `工作台 / 账单猎手`：猎手视图是把 `/new` 塞进 iframe，工作台自己的表单/预览
全部隐藏（`#main.view-hunter > :not(#hunter-pane){display:none}`）。之所以用 iframe 而不是把 React
搬进工作台：猎手是独立构建的 SPA，路由（`/new`、`/g/:id`、`/history`）与样式都已成型，
套进 iframe 只需转发 `/assets` 与这几个路由，零改动它的代码。

## 出片（PDF）

每份账单的 PDF 落在 `$BILLGEN_OUT`（容器里 `/data/renders`，宿主机 `./.data/renders`），文件名形如
`<版式>-<账户>-<任务号前 8 位>.pdf`。主服务按文件名发出来：`GET /api/v1/renders/<文件名>`
（只认文件名，`/`、`..`、非 `.pdf` 一律 404；解析后必须落在出片目录内）。

结果页「PDF 出片」卡片把 `result.renders` 逐条列出来（版式 / 账户 / 页数 / 体积 / 模型填字段还是
确定性草稿），点一行换成内嵌预览，另有「新窗口看」「下载」。这块以前是缺的：PDF 一直生成了，
但没有接口也没有界面，只能进容器 `docker cp`。

## 模型配置

配置落在 `$BILL_GEN_DATA_DIR/hunter/settings.json`，容器里就是 **`./.data/billgen/hunter/settings.json`**
（`docker-compose.yml` 用的是 `./.data:/data` 绑定挂载，不是命名卷 —— 文件就在仓库旁边，看得见、
能备份，`docker compose down -v` 或清理未使用卷都带不走它；写盘是 `tmp → fsync → chmod 0600 →
os.replace`，掉电不会留半截，权限不给同机其它用户读）。界面在顶栏「模型配置」：

| 字段 | 作用 | 落到的环境变量 |
|---|---|---|
| `base_url` / `api_key` / `model` | OpenAI 兼容端点 | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` |
| `structured_mode` | `json_schema` / `function_calling` / `json_mode` | `LLM_STRUCTURED_MODE` |
| `tavily_api_key` | 本地化调研 | `TAVILY_API_KEY` |
| `ai_fill` | 让模型自己填模板字段 | `BILLGEN_AI_FILL` |

界面里只剩模型接入。「离线夹具」不是一个开关：`base_url` + `api_key` + `tavily_api_key`
三项齐备就走真实模型，缺任何一项自动落回本地演示数据（`llm_fake` 由 `apply_patch` 推导，
只有显式传 `llm_fake` 才以传入值为准）。

**账单行名 / 目标模板 / 自动挑选个数已经搬出这里**：它们属于「这一次生成」，做在猎手的
「账户与周期」那一步（`templates` / `templateLimit` / `bankName` 随 `POST /api/generations`
一起进请求，见 `flow/packages/core/src/schemas/request.ts`）。`hunter/settings.json` 里
`bank_name` / `templates` / `auto_limit` 仍作为**默认值**导出成 `BANK_DISPLAY_NAME` /
`BILLGEN_TEMPLATES` / `BILLGEN_AUTO_LIMIT`，请求没指定时才用它们。

规则：

0. **先测通再保存**：界面里「测试模型连接」「测试 Tavily」各打一次最小请求
   （`POST /api/v1/hunter/test-llm`、`POST /api/v1/hunter/test-tavily`，body 是还没保存的表单值，
   缺省字段回落到已存配置），两项都通过「保存并重启」才可选；改了对应字段测试结果立刻作废。
   探测走 `src/billgen/hunter/probe.py`，只用 `urllib`（容器里没装 requests）：
   模型先按选定的 `structured_mode` 带 `response_format`，被 400/422 打回再退一次纯对话
   （自建网关对 `json_schema` 的支持参差，不该把方言差异当成故障），Tavily 打一次
   `max_results: 1` 的搜索。失败时把上游状态码和报错正文原样回给界面（401 → 「鉴权失败」，
   404 → 「地址不对，检查是不是漏了 /v1」，连不上 → 「连不上 <url>：<原因>」）。
1. **密钥只写不读**：`GET` 只返回 `has_api_key` / `has_tavily_key`，保存时留空表示沿用旧值；
2. **保存即重启**：`PUT /api/v1/hunter/settings` 落盘后重启子进程，新配置在新进程里生效；
   对话框中会显示 `storage`（配置文件路径、字节数、最后保存时间、是否可写）和 Key 掩码
   （`sk-…3456`）——「保存过没有」不用猜；
3. **校验在前端之外**：`SettingsError` 统一映射成 400 `invalid_settings`，非法 `base_url`、
   未知模板、越界 `auto_limit` 都会被拒；
4. **环境变量仍是默认值**：文件不存在时按环境变量初始化，因此容器编排与旧用法都还成立。

## 部署

`docker-compose.yml` 只发布 `127.0.0.1:${HUB_PORT:-8800}:8800`；猎手在容器内 `127.0.0.1:8797`，
`deploy/entrypoint.sh` 改成单进程 `exec billgen serve --hunter-port 8797`。
`Dockerfile` 的 `EXPOSE`/`HEALTHCHECK` 同步改成 8800 + `/api/v1/hunter/status`。

## 验收

```bash
.venv/bin/billgen serve --port 8823 --hunter-port 8824   # 本地
curl -s :8823/api/v1/hunter/status | jq '.ready'          # true
curl -s -X POST :8823/api/generations -H 'X-API-Key: devlocal' \
     -H 'Content-Type: application/json' -d @request.json  # 202
```

- `tests/test_hunter.py`：配置默认/回退/密钥遮蔽/校验/原子保存、`to_env` 映射、
  以及 HTTP 层对假上游的全套转发（含 SSE 不缓冲、POST 体与 `X-API-Key` 透传、上游不可达 502）；
- 容器实测：`/`、`/new`、`/assets/*`、`/api/v1/hunter/status` 全通，一次作业出 2 份 PDF
  （chase 2 页、boa 4 页），产物落在 `/data/renders`。
