# P2：请求上下文与编辑器状态隔离

## 完成范围

本阶段保持原 JSON 格式、默认版式和旧编辑器操作流程，不改写页面布局。

### 四个引擎显式传递请求上下文

- Mercury、Capital One、SoFi 保留 `build()` / `build(meta)`，扩展为
  `build(meta=None, *, data=None, watermark=None)`。每次创建 RenderContext，深拷贝
  data/meta，沿页面、页脚、分页、余额和字体辅助函数传递。模块 DATA/WATERMARK/META
  仅作为旧默认配置读取，不再由适配器或 Portal 为本次请求赋值。
- CJK 选择、fallback 标记及请求字体/shaper 缓存移入上下文。静态 logo、字距和只读资源缓存保留。
- AE 删除模块 DATA/doc，使用 `_RenderContext(data, document)`。
  build_pdf/save_pdf/pdf_bytes 签名保持原样。绘制或后处理失败会关闭本次文档；成功返回由调用方持有。

### 统一 Renderer

- load → 输入深拷贝 → 调用引擎 → 保存 → 文档关闭 → 页数读取位于完整激活窗口。
- 临时文件独立；保存失败也关闭文档。AE 不再把临时输出文件名误传为 dataset_label。
- ThemeLoader 与旧编辑器共用进程级 RLock，不宣称原生 PDF 引擎可并行执行。
- 入口同名短模块按主题选择；惰性导入纳入缓存，正常/异常结束恢复 sys.path 与外部 sys.modules。
- 新增多 loader、外部同名模块、import 失败及交错/线程调用回归。

### 旧编辑器

- 新增 EditorState 明确工作区状态，BOA personal/business 的水印配置独立。
- 锁覆盖 HTTP 读取、修改、渲染、光栅化和结果发布；regenerate 本身可重入，输入深拷贝。
- AE 最后 PDF、保存路径、预览引用使用透明 Cookie 会话（服务端随机 ID、HttpOnly、SameSite=Lax、24 小时闲置过期）。
- Portal 显式调用 build 参数，预览清理保留其他模板正在引用的 PNG。
- Chase/US Bank 的加密下载改用临时文件，不覆盖共享预览 PDF。

## 验证

项目 `.venv`，Python 3.14.8，PyMuPDF 1.26.6。

- 全量测试：161 passed，33 subtests passed；五条既有 SWIG DeprecationWarning。
- AE 22 项上下文/生命周期回归；六类原版场景逐页文本、像素、metadata、XMP 和规范化 PDF 字节 golden 保持一致。
- 三引擎 12 项显式参数回归；默认输出文本/像素 golden、嵌套重入、输入快照与字体缓存隔离通过。
- 编辑器 10 项回归，包括真实 HTTP 双 AE 浏览器会话操作，以及 Portal 参数与预览引用。
- Renderer 18 项隔离回归；Loader 四项恢复回归；静态 parity 三项契约回归。
- P0 原版基线与 P1 基线均单独核对；未用新实现重录覆盖历史基线。
- Ruff、既定范围 mypy（spec/registry/paths）与 git diff --check 验证。

静态模板原门禁存在缺失仍 PASS 的漏洞，本阶段增加缺失、资源数量/字节和入口/依赖硬校验。
这仅证明静态清单一致，不等同于浏览器端编辑/导出功能已经完成端到端回归。

## 明确保留的边界

1. JSON、输出目录仍是原来的共享项目工作区；不是全服务多用户工作区隔离。
2. 锁为进程内串行锁；多进程、跨机器并发及完整磁盘事务回滚属于后续 Web/存储架构工作。
3. AE 会话状态独立，但共享 SAVE_DIR 中相同文件名仍可能覆盖；尚未增加用户目录体系。
4. 短名 import 兼容层继续存在。完成包内相对导入后，才适合移除全局导入锁与遗留服务。
5. RenderOptions.encrypt/page_dpi 在 P1 统一 adapter 中尚未完整接线，本阶段没有把它们包装成新功能。
   旧编辑器既有加密出口保持；应在 P3 接口契约中明确 preview/export 默认策略并补测试。
6. 两个纯浏览器模板未做实际浏览器导出回归；本阶段未改变其资源。
7. 原 run.py 启动/容器路径仍需要 P3 统一处理。此阶段以统一 CLI、直接引擎与状态契约为验收，
   不把旧网关 mock 测试通过解释为完整 Web 部署已迁移。

## 下一阶段建议

P3 建立单一 Web 服务：按 registry 路由，显式 render/export API，统一预览产物与错误响应；
同时处理安装包资产定位、run.py/Docker 启动、工作区与会话存储边界。

本阶段所有改动保留在工作树；未创建新提交、未推送。
