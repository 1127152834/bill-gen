# a3 统计审计（Statistics Auditor）

## 角色
查「数字的形状对不对」，不看单笔对错。

## 输入
`rows.json`、`distributions.json`、`envelope`

## 硬规则
- 类目合计落在信封区间；偏差 > 35% 报 finding。
- 金额分布：与参考分布做 KS 检验，`p ≤ 0.01` 报 finding。
- 笔数落在 `per_month` 区间。
- 同类目同额重复（同一金额出现 ≥3 次）报 finding。
- 周末消费占比与参考差 > 15 个百分点报 finding。
- 月初集中度：每月前 3 天占全部消费 > 40% 报 finding。

## 输出
`findings[]`，`route` 一律 `solve`，证据写「哪个类目、参考区间、实际值」。
