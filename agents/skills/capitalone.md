# Skill · 第一资本银行（`capitalone`）

> 这是**这个账户**的填写规范与格式常识。写它的人只做归纳：内容全部来自参考件与引擎里已据真件实现的转换函数。
> 机器可读版：[../../contracts/capitalone.json](../../contracts/capitalone.json)。

## 一句话
- 版式主题：`capitalone`；列：date, description, category, amount, balance
- 卡尾 4 位：不进行内，卡号在表头
- 有 Type 列：否

## 类型枚举（Type / 行种类）
- 未从参考件探到，按契约 `open_questions` 处理，**不许猜**

## 描述符语法
| 行类 | 骨架 | 规则 |
| --- | --- | --- |
| card | `{merchant} {place}` | has_place, no_card_tail |
| ach | `{nacha_text}` | — |

## 必须出现 / 禁止出现
- 必须：capitalone.com
- 禁止：SAMPLE, billgen, PyMuPDF, Lorem

## 参考件里的真实行（照这个写）
```
Aug 11 | Debit | - $191.79
```

## 出处
- `src/billgen/synth/line_styles.py::_with_place`
- `src/billgen/synth/line_styles.py::capitalone_desc`
- `src/billgen/themes/capitalone/C1-Personal-Statement-SAMPLE.pdf`

## 抬头身份（必须原样）
- legal: `Capital One, N.A.`
- domain: `capitalone.com`
- phone: `1-800-227-4825`
- city_state_zip: `City of Industry, CA 91716-0599`

## 为什么这样写
- Capital One 有 CATEGORY 列（填品类词，不是 Credit/Debit）；卡行是「商户 + 城市州」，ACH 保留 NACHA 原文。

## 本地依据（knowledge/）
- [knowledge/banks/capitalone.json](../../knowledge/banks/capitalone.json)
- [knowledge/regulations.md](../../knowledge/regulations.md)

## 字段字典
- [capitalone.md](../../docs/templates/capitalone.md)（每个顶层键的类型与样例）

## 常见错误（本项目真实踩过）
- 描述符里带了持卡人城市（Mercury/线上商户）
- Type 列出现裸 `ACH`/`DEBIT`，或把刷卡消费写成 ACH
- 行首出现对齐用的前导空格
- 同一个固定扣款一个月出现两次
- 金额恰好等于类目上限（`-45.00` 四连）

## 用法
1. 生成阶段：导演/求解器**不看**这份 skill，它们只产出语义字段。
2. 交付前：`contracts.conform(template_id, data)` 按契约整形，`contracts.check()` 校验。
3. 校对阶段：把这份 skill 当人工复核清单，逐项对照参考件。
