# 跑法（Playbook）

## 一次跑一个人、一批月

```
persona  = ①(persona.json)                 # 失败 → 补档案，不猜
for month in months:                       # 12–24 个月一起跑，状态连续
    plan   = ②(persona, ledger[-1], calendar[month])
    ledger = ③(plan, persona)              # 闭合断言失败 → 直接停
    ledger = ④(ledger, poi_table)
    findings = parallel(⑤a1..a6)
    if findings:                           # 最多 2 轮
        plan = ②(persona, ledger[-1], calendar[month], findings=findings)
        ledger = ③(plan) → ④ → ⑤
    render(ledger, contract)               # ⑥
```

## 失败处理

| 情况 | 处理 |
| --- | --- |
| ① schema 不过 / 收支比 > 0.62 | 回档案，让人补信息；不许自动放宽 |
| ③ 闭合断言失败 | 立刻停，修求解器；**不许渲染一个不闭合的账本** |
| ⑤ 只有 `low` findings | 记录，放行 |
| ⑤ 有 `high` findings | 带证据回②重排（≤2 轮） |
| 两轮后仍有 high | `status: needs_human`，输出全部证据 |
| 契约缺失 | 停下补契约（⑤提取器），不许「按默认样式」渲染 |

## 每轮必须留的产物
```
jobs/<id>/
  persona.json
  plan/<YYYY-MM>.json
  ledger/<YYYY-MM>.jsonl
  findings/<YYYY-MM>.json
  <template>-<YYYY-MM>.json          # 交付物
  validation-report.json
  job-summary.json                   # closure / distribution_p / discrimination / attempts
```

## 采样做分布回归
```bash
python -m billgen.lifesim sample --personas 50 --months 10 --seed 7 --out /tmp/sim
python tools/dist_check.py /tmp/sim --against data/distributions.json
```
回归不过 → 不许发版。

## 做一次盲测（每两周一次）
1. 取 20 张真实账单（去标识）+ 从最新跑批里抽 20 张；
2. 同一批交给 3 个模型 + 1 个人，只问「真假 + 依据」；
3. 把被点名的依据归类（金额/商户/地理/节奏/结构），**依据比分数更重要**；
4. 排名前 3 的依据 → 变成对应审计的规则或 `distributions.json` 的参数。

## 反模式（踩过的坑，别再来一次）
- ❌ 让模型直接写金额，再用代码事后矫正（就是我们刚拆掉的那条链）
- ❌ 审计发现问题就去改产物 JSON（治标，下个月重犯）
- ❌ 「跳过找不到的情况」的检查（曾让 11 套模板全绿但其实什么都没查）
- ❌ 用水印/占位词兜底（`SAMPLE`、`CLIENT PAYMENT`、`STORE #1`）
- ❌ 一家银行的描述符规则写死在代码分支里（应由契约决定）
