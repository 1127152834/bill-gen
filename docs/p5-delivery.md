# P5 交付工程化执行记录

## 交付内容

- `.github/workflows/ci.yml`：Ubuntu 24.04 / Python 3.11–3.14 质量矩阵；独立 distribution job 构建 sdist/wheel、独立安装、Docker HTTP 烟测与严格容器黄金回归。不含发布或推送步骤。
- `constraints-ci.txt`：固定本轮验证的直接运行依赖及构建/检查工具；dev extra 补 build/hatchling，消除缺少构建器而跳过 wheel 检查的问题。
- `Makefile`：install/assets/check/build/smoke-wheel/image/smoke-image 常用入口。
- `tools/http_smoke.py`、`tools/smoke_install.py`、`tools/smoke_container.py`：共享 HTTP 验证；全新 venv、离线安装、源码外执行；随机容器端口与精确归属清理。故意失败后的容器清理和 PIP_TARGET/PIP_USER 环境隔离均实测通过。
- `tools/render_regression.py`、`tests/test_render_regression.py`、`tests/fixtures/render_regression_v1.json`：四变体×四类输入×水印开关，共 32 cases/profile。约 119 KiB 指纹，不加入原源码或 PDF。
- `docs/releasing.md`：开发、CI、依赖更新、构建、部署、发布及回滚步骤。

## 可移植黄金回归

双 profile：macOS Arial（3682e06b787c56d68d89）、Debian Liberation（01afb8f3aab66a4a2d84）。每个均与 P4 重构前源码逐页 text/content/RGB72 对照后固化。profile 包含 PyMuPDF/MuPDF、Pillow/FreeType 与四种 donor 字体 SHA256。

未知环境 pytest 明确跳过平台专属断言；严格命令直接失败，CI 独立严格 job 不允许未知环境静默通过。原 Chase 临时源码测试仍可选，但持续固定检查不再依赖临时目录。C1 保留同环境前后字体优化差分，不硬绑定 macOS 字体像素。

Chase personal 的大量交易样例仍固定两页，这是既有行为；其他变体多页案例为 5/8 页，不借测试变更修改原引擎行为。

## 额外部署修复

只读源码挂载发现 Chase 缓存 miss 原先回写包内 assets，导致渲染失败。现有效历史 prepared 只读复用，失效时内存重建，派生产物写用户数据目录 cache/chase。PDF 与三码表作为单个原子 bundle 发布，key 加入实际 Bold donor 字体 SHA256。缓存损坏自动重建，存储不可写回退内存；不改公开入口或 prepared-v1 历史源签名算法。

另将示例初始化测试显式限定自身 BILL_GEN_DATA_DIR，避免继承 Docker /data 干扰测试。

## 最终实测

| 项目 | 结果 |
|---|---|
| macOS / Python 3.14.8 全套 | 306 passed、16 skipped、33 subtests passed |
| Linux / Python 3.12 只读源码全套 | 298 passed、24 skipped、33 subtests passed |
| macOS 严格固定回归 | 32/32 PASS |
| Linux 已安装包严格固定回归 | 32/32 PASS |
| 两份历史 P0/P1 parity | PASS，未重新录制覆盖 |
| Ruff / mypy 既定范围 / diff whitespace | PASS |
| sdist → wheel 构建 | PASS |
| 最终 wheel 独立 venv 离线安装 HTTP | PASS |
| 最终 Docker 镜像构建与 HTTP | PASS |

16 个跳过为可选原源码临时快照比较；Linux 另 8 个为历史 macOS USBank 指纹，由新 Linux profile 覆盖。5 项现有 PyMuPDF SWIG deprecation warnings 留存，不是本次新失败。

独立安装和容器均验证 13 模板清单、旧 BOA 页面/数据、默认数据、预览 job、PDF 与每页 PNG；BOA 为 4 页、297185 bytes，PNG 均 306×396，下载内容摘要与 API 一致。烟测默认不是对所有模板逐一 HTTP 导出；全量渲染由单测与黄金回归覆盖。

## 状态边界

当前版本保持 2.0.0.dev0。未创建提交、tag、推送、发布包或上传镜像。CI 配置已编写，本机执行其主要步骤通过；GitHub 托管四版本矩阵尚未触发，Linux 本机验证是 ARM64 容器，不冒充 GitHub x86_64 托管运行记录。严格 profile 若因系统字体或渲染器平台变化不匹配，会明确失败，需要审阅差异后新增环境。

constraints 不是全量带哈希 lockfile，基础镜像仍用 tag，尚不保证二进制摘要级完全可复现。当前 mypy/lint 遗留豁免、共享编辑器工作区和串行渲染边界见发布文档。P0–P5 计划阶段已落地，后续属于正式发布验收与持续维护。
