# 求解器规格（Solver）

> 这一份是给**实现者**的规格，不是给模型跑的提示词。求解器是纯代码，运行时没有 LLM。

## 职责
把 `plan.json` 的事件表，变成唯一真相 `ledger/<YYYY-MM>.jsonl`：
每一笔有日期、时间、商户类目、金额（分）、方向、余额；并且**构造即闭合**。

## 输入
`plan.json` + `persona.json` + `distributions.json`（真实样本拟合出的参数表）+ `seed`

## 输出：ledger 行
```json
{"id":"2026-11-26#3","date":"2026-11-26","time":"10:42","kind":"holiday_grocery",
 "merchant_class":"supermarket","direction":"debit","amount_cents":16976,
 "balance_cents":486312,"event_id":"e2","notes":"Thanksgiving dinner"}
```

## 四条硬不变量（违反即抛错，不写文件）
1. **闭合**：`begin + Σ(credit) − Σ(debit) == end`；逐行 `balance` 连续。
2. **可行性**：任何时刻 `balance ≥ 0`（除非 persona 允许透支）；发薪前支出不得超过当时的可用余额。
3. **顺序**：行按 `(date, time)` 排序，同日多笔时间不同；跨月 `end(T) == begin(T+1)`。
4. **区间**：每个类目月度合计落在 `persona.envelopes` 内；单笔落在 `CATEGORY_RANGES`。

## 数值生成
- **间隔**：类目到访间隔用负二项（均值/方差由 `distributions.json` 给），周末权重 ×1.35，工作日 ×0.9。
- **金额**：类目 lognormal（中位数=参数表中位价，σ=0.45），再按商家档次乘 0.7/1.0/1.6；
  尾数规则：餐饮/食材多用 `.00/.49/.99`，油价三位小数，订阅固定 `.99`。
- **圆整率**：整美元占比不得超过分布上限（真实数据一般 12%–25%），超了重新抽。
- **抖动**：同类目同额出现两次即触发 ±1–7% 抖动，并保证同类目不撞值（对齐 `postpass.diversify_amounts` 的策略）。
- **日记账再平衡**：若月末合计偏离信封，先动最富余的类目（按 |实际−中位| 排序），单笔调整不超过该笔 25%。

## 与现有代码的关系
- 复用 `src/billgen/gen/envelope.py`（区间）、`seasonal.py`（节日缩放）、`postpass.py` 的
  `_iter_rows/_row_amount/_rescale_amounts`（结构与再平衡）。
- 替换 `pipeline.py` 的 `lifeModel + schedule` 两段；`postpass` 里的金额/类型/去重/排序逻辑
  应该搬进求解器，而不是留在「事后矫正」。

## 验收
- `tests/test_solver_closed.py`：1000 个随机 persona × 12 个月，闭合率 100%。
- 分布回归：与 `distributions.json` 的类目占比、间隔、金额分位做 KS，p>0.01。
- 性能：一个月 < 30ms（12 个月 < 0.4s），可支撑 A/B 大规模采样。

## 角色
（见上：实现者规格）你是把计划变成账本的**唯一数值产地**；运行时无 LLM，纯确定性代码。

## 硬规则
- 违反四条不变量中任意一条 → 抛错，不落盘、不渲染。
- 不读契约、不拼描述符（那是选角与渲染的事）。
- 所有随机性必须来自显式 `seed`，同 seed 重跑结果必须一致。
