# 开发验证与发布操作

当前版本仍为 `2.0.0.dev0`。本次只建立本地与 CI 交付门禁，不创建 tag、不推送镜像、不上传 PyPI。

## 环境与依赖

使用 Python 3.11–3.14。CI 在 Ubuntu 24.04 执行四版本矩阵；容器使用 Python 3.12 slim。安装 Liberation 与 Noto CJK 字体，再运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c constraints-ci.txt -e '.[dev]'
.venv/bin/python scripts/assets.py hydrate
.venv/bin/python scripts/assets.py check
```

`constraints-ci.txt` 固定已验证的直接依赖与主要构建/检查工具，不是含哈希的全量跨平台 lockfile。系统字体、基础镜像 tag 和间接依赖仍可能变化；严格黄金环境指纹用于显式发现漂移，而不是自动覆盖样本。未来更新依赖须开独立变更，检查文本、字形与像素差异后再审阅采纳新 profile。

## 持续门禁

```bash
make PYTHON=.venv/bin/python check
```

包括 Ruff、既定范围 mypy、pytest 和严格固定回归。严格回归只接受已记录的渲染器/字体环境；新环境需先按指纹工具说明核对，不应直接重录当作通过。

CI 分为：

- quality：Python 3.11/3.12/3.13/3.14，LFS 资产、lint、types、测试和 wheel 资源检查；保留 JUnit 结果。
- distribution：构建 wheel/sdist、离开源码的独立 venv 离线安装烟测、Docker 构建与 HTTP 烟测、受控容器内严格黄金回归；仅保留构建产物，不发布。

在未识别的字体环境中，平台专属黄金测试可能明确跳过；CI 的独立严格容器检查负责阻止未知 profile 静默通过。历史包内 `billgen parity verify` 是最初 macOS 字体环境的基线，不把它冒充为所有系统通用像素标准。

## 安装包

```bash
make PYTHON=.venv/bin/python build
make PYTHON=.venv/bin/python smoke-wheel
```

先显式联网准备 wheelhouse，实际 smoke 脚本创建全新临时 venv，仅使用 `--no-index` 安装，清除 PYTHONPATH/开发路径影响，在源码外启动 console entrypoint。检查健康、模板列表、旧编辑器数据、预览、PDF 和 PNG 下载；结束时清理启动的进程与临时文件。

检查 sdist 可重建性时，在临时目录解压后执行 `python -m build --wheel --no-isolation`，确认 wheel 包含示例、版式、字体、静态页面和运行时兼容入口；不得把工作区用户 JSON、备份和预览产物混入安装包。

## 容器

```bash
make PYTHON=.venv/bin/python IMAGE=billgen:local smoke-image
```

脚本只运行已存在的镜像，随机映射本机回环端口，执行相同 HTTP 检查，最终只清理自己创建的精确容器 ID。持久运行：

```bash
docker run --rm -p 127.0.0.1:8800:8800 -v billgen-data:/data billgen:local
```

容器通过 constraints 安装依赖；构建时仍需要系统软件源、Python 包源，若资产是 LFS 指针还需资源下载。没有宣称离线镜像构建或可复现到相同二进制摘要。旧编辑器采用共享工作区；它不是多租户并发服务。

## 发布清单

1. 在干净检出与 LFS 就绪环境运行全部 CI，确认严格回归没有未知 profile。
2. 核对版本、变更说明、公开样例和许可；核对新增字体/资源的分发说明。
3. 从 sdist 重建 wheel，并重复独立安装与镜像烟测。
4. 保存 wheel/sdist 的 SHA256、Python/渲染器/字体版本与 CI 日志。
5. 人工确认正式版本号、tag、目标包索引和镜像仓库之后，再执行独立发布操作；当前工作流无发布凭证、无上传步骤。
6. 回滚时恢复上一安装包/镜像，保留数据卷备份；数据目录与包资源分离，本轮没有数据格式迁移。

## 已知边界

- 当前 mypy 只检查 spec、registry、paths；themes 沿用历史 lint 豁免，未宣称类型与风格全覆盖。
- 浏览器端 Ally/Alliant 的真实导出仍需浏览器验证；HTTP smoke 检查静态页面与服务端模板，不把静态资源存在当作浏览器 PDF 等价证明。
- CI 配置落地与本机执行成功，和 GitHub 托管 runner 实际运行成功是两回事；没有推送就不会触发远程 CI。
