# 渲染（Renderer）

> 纯代码阶段，无 LLM。

## 职责
把 `ledger` 按契约投影成该银行模板的月 JSON，再出 PDF。

## 步骤
1. `contracts.conform(contract_id, data)`：按契约改描述符（去城市、去 `PAYMENT` 尾巴、裁剪长度）、归一 Type。
2. `monthpack.month_artifacts(...)`：按账期切月，写 `<template>-<YYYY-MM>.json`。
3. `registry.get_renderer(template).render(data, RenderOptions(watermark=False))`：出 PDF。
4. `contracts.check(contract_id, data)`：残留问题写进 `contract_warnings`。
5. `tools/realism_rules.py`：版式闸门（字体、页码、磁码、摘要会计）。

## 硬规则
- 渲染器**不许**改数字，只能改表现形式。
- `watermark` 默认 False：用户自己的账单不许印 SAMPLE。
- 契约缺失 → 报错停下，不许「按默认样子」渲染。
- 出片后必须回读文本层做一次 `SAMPLE`/占位词检查。

## 角色
（见上）你是投影器：把账本按契约变成该银行的月 JSON 与 PDF，不改任何数值。

## 输入
`ledger/<YYYY-MM>.jsonl` + `contracts/<template>.json` + `monthpack` 字段字典
