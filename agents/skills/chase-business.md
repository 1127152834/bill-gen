# Skill · 大通银行 · 商业账单（`chase-business`）

> 这是**这个账户**的填写规范与格式常识。写它的人只做归纳：内容全部来自参考件与引擎里已据真件实现的转换函数。
> 机器可读版：[../../contracts/chase-business.json](../../contracts/chase-business.json)。

## 一句话
- 版式主题：`chase`；列：date, description, amount, balance
- 卡尾 4 位：印在行尾（`… Card 4499`）
- 有 Type 列：否

## 类型枚举（Type / 行种类）
- Card Purchase
- ACH Credit
- ACH Debit
- Zelle Payment
- Check Paid
- Fee
- Deposit
- Withdrawal

## 描述符语法
| 行类 | 骨架 | 规则 |
| --- | --- | --- |
| card | `{merchant} {city} {state} Card {last4}` | has_card_tail, has_place |
| ach | `{payee}` | upper |
| zelle | `Zelle Payment to {party}` | upper |
| check | `Check Paid {date} {number}` | upper |

## 必须出现 / 禁止出现
- 必须：chase.com, 1-800-935-9935, JPMorgan Chase Bank, N.A.
- 禁止：SAMPLE, billgen, PyMuPDF, Bank of America

## 参考件里的真实行（照这个写）
```
02/02 | Deposit      1264675874 | 23,000.00
```
```
02/05 | Deposit        29478128 | 20,467.15
```
```
02/05 | Deposit      2173594309 | 13,460.37
```
```
02/05 | Deposit      1261835474 | 8,390.25
```

## 出处
- `src/billgen/synth/adapters/chase.py::_business_desc`
- `src/billgen/synth/adapters/chase.py::_personal_desc`
- `src/billgen/synth/adapters/chase.py::build_data（label + SUB_PAD 与 desc 拼在同一字段）`
- `src/billgen/themes/chase/assets/chase_static_business.pdf`
- `src/billgen/themes/chase/assets/chase_static_business.pdf#p1`
- `src/billgen/themes/chase/assets/chase_static_business_prepared.pdf`
- `src/billgen/themes/chase/assets/chase_static_personal.pdf`
- `src/billgen/themes/chase/assets/chase_static_personal.pdf#p1`
- `src/billgen/themes/chase/assets/chase_static_personal_prepared.pdf`

## 抬头身份（必须原样）
- legal: `JPMorgan Chase Bank, N.A.`
- domain: `chase.com`
- phone: `1-800-935-9935`
- city_state_zip: `San Antonio, TX 78265-9805`

## 为什么这样写
- Chase 个人/企业对账单都把卡尾印在行末（Card 4499）；企业版行标签用 Deposit/Withdrawal，不是 Card Purchase。；desc 字段带行标签与对齐空档，校验前要先切出描述符本体

## 本地依据（knowledge/）
- [knowledge/banks/chase.json](../../knowledge/banks/chase.json)
- [knowledge/regulations.md](../../knowledge/regulations.md)

## 字段字典
- [chase-business.md](../../docs/templates/chase-business.md)（每个顶层键的类型与样例）

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
