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

## 模型配置

配置落在数据卷 `$BILL_GEN_DATA_DIR/hunter/settings.json`，界面在顶栏「模型配置」：

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

1. **密钥只写不读**：`GET` 只返回 `has_api_key` / `has_tavily_key`，保存时留空表示沿用旧值；
2. **保存即重启**：`PUT /api/v1/hunter/settings` 落盘后重启子进程，新配置在新进程里生效；
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
