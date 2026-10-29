# P3：单一 Web 服务与安装部署

## 本阶段结果

新增 `billgen serve`，旧 `python run.py` 变为薄兼容入口。一个 HTTP 服务同时提供：

1. 首页与 `/healthz`。
2. 基于 registry 的 `/api/v1/` 模板、默认数据、预览、导出与产物下载 API。
3. 原 10 个编辑器挂载，覆盖 13 个模板。

不再创建 8791–8798 子服务，不再反向代理或依赖 Referer。保留表单、旧 JSON 数据格式、
样例、水印、版式、下载和恢复流程，不在这一阶段统一改写前端框架。

## 架构

- `server/http.py`：传输层、JSON 解析、请求相关 ID、HTTP 状态及产物链接。
- `server/application.py`：模板能力、数据/选项验证、渲染、PNG 和元数据、有界产物缓存。
- `server/legacy.py`：原 handler 的同进程兼容挂载；捕获响应后精确补齐 API/预览 URL 前缀。
- `server/bootstrap.py`：只读示例到可写工作区的独占初始化，已有文件保持。
- `registry` / `engine`：新 API 的模板入口和渲染实现。

旧 handler 继续保留其专用导出/保存流程，并非已经全部改为调用新 API。
兼容层的短名导入、执行和响应捕获共享 RENDER_LOCK；网络正文读取和网络响应写入在锁外。
统一 API 每次深拷贝输入，产物使用不可预测的独立 ID，request_id 只用于关联日志。

## 启动

```bash
pip install -e '.[dev]'
python scripts/assets.py hydrate
billgen serve --host 127.0.0.1 --port 8800
# 或 HOST=127.0.0.1 GATE_PORT=8800 python run.py
```

默认数据位置：macOS `~/Library/Application Support/billgen`；其他平台按 paths.user_data_dir 定义。
`BILL_GEN_DATA_DIR` 必须是绝对路径。启动只创建缺失的 8 份示例及 8 份 reset 备份，
不覆盖既有数据；无磁盘示例的引擎继续使用各自模块内置默认数据。

wheel 包含只读 examples、manifest、runtime_paths shim 和资产；排除可写数据、bak、预览。
默认数据缺失时，从独立打包的合成 examples 按 manifest 映射读取。LFS 脚本复用搬迁映射。

```bash
docker build -t billgen .
docker run --rm -p 8800:8800 -v billgen-data:/data billgen
```

## API 契约

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/api/v1/templates` | 13 项及实际能力；两项浏览器模板仅静态入口 |
| GET | `/api/v1/templates/{id}/data` | 独立默认数据副本 |
| POST | `/api/v1/templates/{id}/preview` | `{data?, options?}`，返回 201 与产物信息 |
| POST | `/api/v1/templates/{id}/export` | 同上，使用导出默认策略 |
| GET | `/api/v1/jobs/{job_id}` | 信息及 self/pdf/pages 链接 |
| GET | `/api/v1/jobs/{job_id}/document.pdf` | PDF |
| GET | `/api/v1/jobs/{job_id}/pages/{index}.png` | PNG，索引从 0 开始 |

- options 支持 watermark、encrypt、page_dpi 及适用模板 font_policy。
- preview 默认未加密；export 默认仅 AE 加密，其余与旧导出一致为未加密。
- AE、Chase、US Bank 支持显式 encrypt；其余引擎请求 True 返回 unsupported_option。
- page_dpi 控制统一 API 的 PNG 预览，不改变 PDF 页面尺寸。
- JSON 上限 4 MiB，应用数据上限 2 MiB；限制页数、每页像素与产物总量。
- 缓存最多 32 任务 / 128 MiB，创建后 TTL 900 秒，超限按最近访问淘汰；不持久化，重启清空。
- 错误 `{error:{code,message}, request_id}`，使用对应 400/404/413/415/422/500 状态，
  对外不传内部 traceback；旧接口保持原错误契约。

## 验证与证据

- 全量 Python 回归：231 passed、1 skipped、33 subtests passed（保留五条 SWIG 弃用警告）。
  Ruff、既定三核心文件 mypy、两份历史 parity 与 diff 空白检查全部通过。
- 真实 HTTP：全部 10 编辑器页面、8 动态 API、WF 生成/PDF/PNG、AE cookie 路径、
  跨主题 import 身份、HEAD、目录逃逸及请求长度限制。
- 新 API：实际 PDF/PNG 下载、元数据、未知模板、过期/缺失任务、错误正文。
- 实际浏览器：BOA 修改字段后失焦触发生成，四页预览加载，成功下载 PDF；
  Ally、Alliant 均完成浏览器端 PDF 导出。
- 浏览器检查发现旧泛字符串替换会损坏 JS 正则和 Ally PDF PostScript 字符串，
  已收紧 URL 前缀改写并保留 vendor/静态脚本内容；重启最新服务后 Alliant 初始化与导出通过。
- wheel 安装后离开源码 cwd，从临时目录启动：BOA/WF 新版 data=200、preview=201，
  旧 `/boa/api/data`、`/wf/api/data` 均为 200。
- 实际 Docker build 与同套接口烟测通过；docker top 确认为单个 billgen serve 主进程。
  测试容器结束后已清理。
- 两份历史基线未重录；以页数、文本、尺寸及字体等原判据核对。
  加密接线引入 AE 输出字节数变化，字节级相同不是该 parity 门禁的判据。

## 边界与下一步

1. 使用标准库 HTTP 服务，面向原本地工具的迁移；尚无账户、访问控制和多租户隔离。
2. 原编辑器仍共享工作区（AE 保留其浏览器会话），相同文件名仍可能覆盖。
3. 渲染由进程内锁串行；没有分布式任务队列或跨进程一致性协议。
4. 产物 ID 是本地服务的访问句柄，不是用户身份；缓存未按账户划分。
5. 注册表掌管新 API，旧表单字段与旧 handler 的模板专用细节仍由兼容层保留。
6. 未证明所有浏览器、所有极端数据下的逐像素等价；现有 golden/parity 与明确列出的浏览器烟测通过。
7. P4 再处理双胞胎引擎重复、包内相对导入及 C1 字体体积；P5 再完善 CI 与发布。

本阶段未创建提交，未推送。
