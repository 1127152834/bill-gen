# P4：公共引擎能力抽取与字体体积治理

## 目标与结果

保持 13 个模板入口、编辑器操作、JSON 字段、默认值、分页和原有输出行为；继续使用现存 P0/P1 基线，不重新录制覆盖。未创建提交或推送。

本轮完成三项生产调整：

1. US Bank 与 Chase 抽取精确重复的基础能力，保留个人/商业布局分歧。
2. 内置引擎与编辑器改为规范包导入；内置 ThemeLoader 不再切换短名模块或注入 sys.path。
3. Capital One 仅缩小嵌入字体程序流，保持 PDF 字体字典和内容流。

## US Bank

新增 `src/billgen/themes/usbank/shared.py`，抽取 20 个同构定义：CMap 解析、UsFont、escaping、layout 构建、金额/日期/地址、回放快照及 metadata。metadata 默认值与时钟通过参数传入；各版本保留旧入口包装。

| 生产源码 | 调整前行数 | 调整后行数 |
|---|---:|---:|
| generate_us.py | 1203 | 922 |
| generate_biz.py | 1474 | 1190 |
| shared.py | 0 | 315 |
| 总计 | 2677 | 2427 |

净减少 250 行（9.34%）。保留 Page、us_calc、flow_engine 分支、字体来源页、公户字形追加和各自 FONTS 对象；共享类不等于共享可变字体状态。

默认、空交易、70 条带两行 extra 的多页、replay 共 8 组原实现对照：逐页文本和 72dpi RGB 像素摘要一致。新增 22 项常规测试覆盖 CMap、多字符映射、字体 ToUnicode/W 增补、金额、metadata、加密权限及输入保持。

## Chase

新增 `src/billgen/themes/chase/common.py`：显式 FontCodec、prepared 缓存签名/打开逻辑、9 个纯辅助函数，以及参数化 metadata/watermark 能力。

FontCodec 接收个人/商业版自己的字典引用，prepared 重建后的字典更新仍可见；BASE、TEMPLATES、字体扩展与 F3 回调显式注入。保留 `prepared-v1` 算法，保留 Page、make_units、金额尾空格和流水布局差异。

两个生成器及 common 合计 3252 → 3148 行，净减少 104 行、5009 字节。旧 `build_pdf(d, out_path, watermark=True)` 签名保持。

两版 × 默认/空交易/70 笔多行/replay × 水印开关，共 16 项原实现黄金对照：页数、逐页文本、像素、解压内容流一致。另有 9 项常规测试覆盖字体状态独立/热更新、prepared 命中/失效、回放稳定及输入保持。

## 包导入边界

- BOA 的函数体内 orig_embed、Wells Fargo 的 declared_fonts 均改为完整主题包导入。
- Chase/US Bank 的 chstream、barcode、字体辅助模块保留原变量名，但来源是各自主题包。
- 所有 Python 编辑器与 Chase 分发层去掉主题目录 sys.path 注入，使用所属主题的完整包路径。
- 内置 ThemeLoader.load 返回标准 importlib 包模块；多个 loader 与旧编辑器共用同一规范身份。
- 内置 activated 只保留共享 RENDER_LOCK，不修改 sys.path 或短名 sys.modules。
- 外部旧式目录仍保留原隔离加载路径，原外部主题测试继续通过；本轮未宣称全面删除兼容层或解锁并行渲染。

新增 AST 门禁检查主题内短名同级导入和 sys.path.insert；另测外部同名模块不受影响以及规范模块身份。

## Capital One 字体子集

原实现无条件嵌入完整 CJK 字体，即使英文默认数据也约 10.6 MB。直接使用 MuPDF 原生 subset 对 TTC/CFF 效果有限；fallback 子集路径会改变字体名并丢失 .notdef 轮廓，故未采用。

最终方案 `_subset_cjk_font(doc, *, fallback_used=False)`：

- 从 texttrace 收集原始 glyph ID，fontTools 使用 retain_gids。
- 保留 .notdef outline，保持原来缺字时的显示；这不是新增字符覆盖能力。
- 保留 ASCII 32–126，避免 spacewidth 度量变化。
- 只替换原 FontFile2/FontFile3 流；不改 BaseFont/FontName、ToUnicode、W、页面内容、Info/XMP。
- 无法匹配字体或子集收益不足时保留原程序；异常产生 RuntimeWarning，保留全量输出。

| 用例 | 原始 PDF | 优化 PDF | 减少 |
|---|---:|---:|---:|
| 默认，3 页 | 10,609,955 B | 598,147 B | 94.36% |
| 中日韩混合，3 页 | 10,610,278 B | 616,776 B | 94.19% |
| 65 笔交易，6 页 | 10,718,444 B | 710,685 B | 93.37% |

以上为独立主题测试样例，非历史 baseline 的同一组数据。历史探针实际记录 10,624,694 → 612,891 B，减少约 94.2%。

三例 72/144dpi 逐页像素、rawdict、texttrace（含 glyph ID/spacewidth）、字体名清单、内容流和元数据均一致。Office PDFium 渲染成功且 missingFonts=[]；本轮未完成人工视觉审阅，像素对照属于客观回归而非人工评审。

权衡：同进程串行默认样例 warm 中位数 0.7929 → 1.0650 秒（约 +0.272 秒、+34.3%）；首次使用测量 0.8096 → 1.0647 秒，非冷 OS 缓存测试。没有用更快的渲染换取丢字或改版式。

## 验证记录

- Ruff 全工程既定范围 PASS；themes 仍在历史 extend-exclude 内，新增公共模块另经子任务定向检查。
- mypy 既定 3 文件（spec/registry/paths）PASS；未宣称全项目类型检查。
- git diff --check PASS。
- 启用 Chase 临时原源码对照后全套：284 passed、1 skipped、33 subtests passed；5 项既有 PyMuPDF SWIG deprecation warnings。
- 唯一跳过项是环境缺少 hatchling 的自动 wheel 构建测试；本轮未重新执行 Docker/wheel 独立部署烟测，P3 已有记录。
- 全套包含旧编辑器生成/预览、统一 HTTP API、请求隔离、包定位等回归。本轮未重新执行浏览器手动点击测试。
- P0 和包内 P1 两份历史 parity 均 PASS；字体名无需归一，未更新历史文件。

复跑常规门禁：

```bash
.venv/bin/python -m pytest -o addopts='' -q
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/billgen parity verify
.venv/bin/billgen parity verify --baseline ../bill-gen-refactor/baseline.json
git diff --check
```

Chase 原实现快照只在本次临时目录，不进入分发包。设置 CHASE_ORIGINAL_SOURCE_DIR 后额外执行 16 项黄金对照；未设置时常规 9 项仍执行，黄金对照明确 skip。US Bank 与 C1 原实现临时快照同样属于本轮实验材料，不是持续 CI 基础设施。P5 应固定可移植黄金材料/指纹并建立持续门禁，避免依赖本机临时文件。

## 后续 P5

建立 Python 版本矩阵 CI、资产就绪与打包检查、镜像构建/运行检查、发布说明和可移植回归基准。保留变体特有布局而非追求行数最少；主题全量 lint/type 收紧可逐步纳入，不把当前兼容锁等同于并发吞吐提升。
