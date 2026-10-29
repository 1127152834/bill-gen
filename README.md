# bill-gen

原始「账单模板整合版」的独立源码仓库：保留各银行原有的 HTML/JavaScript 编辑器、Python/浏览器 PDF 生成引擎，以及 `run.py` 单端口统一入口。

这份源码来自原始整合版，不包含 LedgerLab、LangGraph 流水生成 Demo 或公司工作台的 React 页面。它用于编辑输入资料、预览版式和导出 PDF；不连接银行，也不读取真实银行交易。

## 包含的模板

| 入口 | 模板 | 实现 |
| --- | --- | --- |
| `/wf/` | Wells Fargo | HTML 编辑器 + Python |
| `/ae/` | American Express Rewards Checking | HTML 编辑器 + Python |
| `/merc/` | Mercury、Capital One、SoFi | 共用编辑器 + 3 个 Python 引擎 |
| `/boa/` | Bank of America 个人、企业 | 共用编辑器 + Python |
| `/chase/`、`/chasebiz/` | Chase 个人、企业 | HTML 编辑器 + Python |
| `/us/`、`/usbiz/` | U.S. Bank 个人、企业 | HTML 编辑器 + Python |
| `/alliant/` | Alliant Credit Union | 浏览器编辑与 PDF 导出 |
| `/ally/` | Ally Bank | 浏览器编辑与 PDF 导出 |

共 13 种模板。统一入口默认 `http://127.0.0.1:8800`；Python 子服务只绑定回环地址的 8791–8798 端口。

## 首次运行

需要 Python 3.12+；Docker 方式会安装所需字体及 Python 依赖。

```bash
git clone https://github.com/1127152834/bill-gen.git
cd bill-gen
python3 scripts/assets.py import /absolute/path/to/整合版
python3 scripts/assets.py check
python3 scripts/seed_examples.py
docker compose -p bill-gen up -d --build
```

打开 `http://127.0.0.1:8800`。自定义宿主端口可用 `HUB_PORT=9080 docker compose -p bill-gen up -d --build`，也可执行 `bash docker部署整合版.sh 9080`。

原始模板依赖字体、图片、PDF 底稿和版式数据，其中一部分保留了历史账单内容。因此这些资源未上传公开仓库。导入脚本只从你自己的原始「整合版」复制清单中的资产，并抽取两处源码内嵌的资产数据；不导入客户编辑 JSON、凭据或历史输出。缺少资产时启动会明确报错，不生成不完整文件。

资源清单位于 `scripts/private-assets.json`，导入后仍受 `.gitignore` 排除。请保留本机原始资产备份；仅克隆源码、未导入资产时，无法完整预览或导出。

## 本机运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/assets.py import /absolute/path/to/整合版
python run.py
```

Linux 需要 Liberation Sans 和 Noto CJK 字体；macOS 可使用系统 Arial。可设置 `STATEMENT_FONT_DIR` 与 `STATEMENT_CJK_FONT` 指定字体位置。Windows 本机运行未验证，建议使用 Docker。

首次启动会从 `examples/` 初始化合成示例。已有客户编辑数据不会被覆盖；这些运行数据、浏览器本地存储和生成结果都不属于源码交付。Docker 只挂载各编辑器的数据 JSON 到本机同名文件，更新镜像时不会被旧卷里的代码覆盖；首次 Compose 启动前须执行上面的示例初始化命令。

本项目保持原始本地工具的使用方式，不含登录或多用户隔离；Compose 默认只向本机开放端口。银行名称及标识属于相应权利人，模板及默认数据不能作为银行签发的记录或当前产品条款。

## 目录

```text
run.py                     统一入口与反向代理
AE/、BOA/、富国/、大通/、USbank/  原始编辑器与 Python 引擎
水星C1sofi/                 共用编辑器及 3 个模板引擎
alliant/、ally bank/        原始静态编辑与 PDF 导出
examples/                  合成默认资料
scripts/                   私有资产导入、检查和示例初始化
runtime_paths.py           跨平台字体定位
tests/                     入口、导入与初始化回归测试
Dockerfile、docker-compose.yml  独立运行配置
```

公开副本只做默认数据替换、私有资产拆分、路径可移植处理和必要的启动收尾修复；原编辑界面与模板核心实现均予以保留。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q run.py runtime_paths.py scripts AE BOA 富国 大通 USbank 水星C1sofi
```

本地拥有原始资产时，再执行 `python3 scripts/assets.py check`，启动后逐个打开上表入口检查预览与导出。测试仅使用合成记录。

发布前已验证：13 项自动测试、独立 Docker 构建、统一入口及 10 个编辑入口、11 个 Python 模板的 PDF 导出。Alliant 与 Ally 的浏览器导出另行通过实际页面检查。
