# 06 接入自查（给开发者，不是给模型）

上线前逐条打勾，缺一条就别开流量。

## 契约
- [ ] 每个账户一份契约，字段齐（`columns`/`type_enum`/`descriptor`/`row_rules`/`forbidden_literals`/`evidence`）
- [ ] 每份契约的每条规则都有出处（样本页码或代码符号）
- [ ] 没有出处的写进 `open_questions`，且运行时产生 warning
- [ ] `statement_lint.py` 对你现有产物跑出 0 问题

## 接口
- [ ] `read_contract` / `emit_plan` / `solve` / `render` 四个接口就位
- [ ] `conform` 全流程只有一个调用点，且在 `lint` 之前
- [ ] 关闭模型后系统仍能跑（降级路径存在）

## 数据
- [ ] `seed = sha256(account + month + profile_id)`，同 seed 出同一份数据
- [ ] `manifest.json` 记录 seed / 契约 sha256 / 模型名 / tokens
- [ ] 金额只由求解器产生（代码里 grep 不到「LLM 写 amount」）

## 观测
- [ ] 日志是「阶段 + 人话 + 数字」，不是一坨 JSON
- [ ] 失败有明确回退阶段，不是「重跑一次看看」

## 验收
- [ ] 闭合率 100%
- [ ] lint 0 问题
- [ ] 分布 KS p > 0.01
- [ ] 地理/日历一致性 0 例外
- [ ] 盲测辨识率 ≤ 65%（记录在案）
