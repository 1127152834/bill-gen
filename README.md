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

需要 Git、Docker 和 Docker Compose。宿主机无需安装 Python 或 Git LFS。

```bash
git clone https://github.com/1127152834/bill-gen.git
cd bill-gen
docker compose up -d --build
```

打开 `http://127.0.0.1:8800`。首次构建会安装 Python 依赖和 Linux 字体，并校验全部模板资源。自定义宿主端口可用 `HUB_PORT=9080 docker compose up -d --build`，也可执行 `bash docker部署整合版.sh 9080`。

## 模板资源与 Git LFS

全部运行所需的字体、图片、PDF 底稿、版式数据和浏览器依赖已随仓库提供，默认资料均为合成示例。无需从其他目录导入资源，也无需手工初始化 JSON。

二进制资源通过 Git LFS 存储。已安装 Git LFS 时，克隆会正常检出资源；没有 Git LFS、或克隆时跳过 LFS 下载时，Docker 构建会自动识别资源清单内的指针，通过此公共仓库的 GitHub LFS 接口下载，并核验 SHA-256 与字节数后才写入镜像。构建过程不需要 GitHub Token，不读取本机 Git 凭据，也不打印下载签名 URL。资源清单保留旧文件名 `scripts/private-assets.json`。

首次构建需要联网访问 Python 包仓库、系统包仓库和 GitHub LFS。网络不可达、LFS 配额不足、对象尚未上传或校验失败时会明确中止构建；不会把指针文件当作字体/PDF继续启动。网络恢复后重新执行构建即可。正常镜像启动不再下载模板资源。

## 编辑数据与升级

Compose 使用单独的命名数据卷 `bill-gen-data`，挂载到 `/data`，由 `BILL_GEN_DATA_DIR` 指定。启动器只为缺失文件写入合成默认数据，已有编辑 JSON 和恢复备份保持不变；更新镜像不会把旧编辑器代码藏在数据卷里。

BOA、Wells Fargo、Chase 和 U.S. Bank 的编辑 JSON、服务器生成的预览，以及 AE 的“保存 PDF”结果位于该卷。Mercury、Capital One、SoFi、Alliant 和 Ally 仍按原编辑器约定使用浏览器本地存储；AE 表单使用原有页面状态，下载的文件保存在浏览器下载目录。

更新时执行 `git pull` 和 `docker compose up -d --build`。`docker compose down` 保留数据卷；加 `-v` 会删除卷中的编辑数据和已保存文件，请仅在确认需要清空时使用。备份可使用界面中的 JSON/PDF 导出功能；不要把运行数据提交到 Git。

## 本机运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/assets.py hydrate
python run.py
```

Linux 需要 Liberation Sans 和 Noto CJK 字体；macOS 可使用系统 Arial。可设置 `STATEMENT_FONT_DIR` 与 `STATEMENT_CJK_FONT` 指定字体位置。Windows 本机运行未验证，建议使用 Docker。

本机启动同样会从 `examples/` 初始化缺失的合成示例。未设置 `BILL_GEN_DATA_DIR` 时保持原目录结构；设置后写入该绝对路径。旧版 `scripts/assets.py import` 仅为已有本机安装保留，默认运行流程无需使用。

本项目保持原始本地工具的使用方式，不含登录或多用户隔离；Compose 默认只向本机开放端口。银行名称及标识属于相应权利人，模板及默认数据不能作为银行签发的记录或当前产品条款。

## 目录

```text
run.py                     统一入口与反向代理
AE/、BOA/、富国/、大通/、USbank/  原始编辑器与 Python 引擎
水星C1sofi/                 共用编辑器及 3 个模板引擎
alliant/、ally bank/        原始静态编辑与 PDF 导出
examples/                  合成默认资料
scripts/                   LFS 资源补齐、检查和示例初始化
runtime_paths.py           跨平台字体定位
tests/                     入口、导入与初始化回归测试
Dockerfile、docker-compose.yml  独立运行配置
```

公开副本保留原编辑界面与模板生成逻辑，使用合成示例，并提供资源校验、跨平台路径和独立持久数据目录。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q run.py runtime_paths.py scripts AE BOA 富国 大通 USbank 水星C1sofi
```

执行 `python3 scripts/assets.py check` 可检查资源是否齐全；若本地仍是 LFS 指针，先执行 `python3 scripts/assets.py hydrate`。启动后逐个打开上表入口检查预览与导出。测试仅使用合成记录，LFS 单测用内存传输模拟，不依赖真实网络。

自动测试覆盖资源路径、LFS 下载及校验、临时请求头的重定向边界、合成数据初始化和已有编辑数据保护。发布验收从仅含 LFS 指针的新克隆执行 Compose 构建，检查全部 13 个模板的预览与导出，并验证容器重启后编辑数据仍然保留。
