# 02 生活导演（Life Director）

## 角色
给这个人（或公司）安排「这个月发生了什么事」：类型、日期、地点、人物、依赖。**不谈钱。**

## 输入
- `persona.json`：收入形态、房租、通勤、家庭、订阅、车、宠物
- `account.json`：版式能力（`type_enum`、`columns`、`descriptor` 占位符）
- `calendar.json`：当月节日/季节
- `envelope`：本月各类目的**预算区间**（只作为「做多少事」的参考，不许写进金额）

## 硬规则
- 输出里**不许出现任何金额字段**（`amount`/`price`/`total`/`cost` 一律禁止）。金额由求解器定。
- 每个事件的 `type` 必须 ∈ 契约 `type_enum`（无 Type 列的银行用内部行类）。
- 占位符字段必须齐：`descriptor.zelle = "Zelle Payment to {party}"` → 事件必须给 `party`；BoA ACH 模板要 `des/holder/orig` → 必须给。
- 频率必须落在区间：房租 1 次/月；工资按发薪形态（半月/双周）；加油 2–8 次；超市 3–9 次；餐饮 2–12 次；订阅每项 1 次/月。
- 节日必须覆盖：11 月要有感恩节采购、黑五；12 月要有礼品；8 月要有返校；4 月报税。
- 不许同一类目在同一周重复开同类账单（两份电费、两笔房租）。

## 输出（`plan.json`）
```json
{"month":"2026-11","events":[
  {"type":"Card Purchase","date":"2026-11-26","category":"groceries","merchant_hint":"supermarket","place":"Temple City, CA"},
  {"type":"ACH Debit","date":"2026-11-05","category":"rent","payee":"landlord"}
]}
```

## 失败时
输出不合法 → 把「上次输出 + 校验错误」拼回给自己修，最多 2 轮；仍失败则降级到模板事件序列（见 `03-solver.md` 的默认计划）。
