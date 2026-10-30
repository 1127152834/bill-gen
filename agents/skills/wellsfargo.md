# Skill · 富国银行（`wellsfargo`）

> 这是**这个账户**的填写规范与格式常识。写它的人只做归纳：内容全部来自参考件与引擎里已据真件实现的转换函数。
> 机器可读版：[../../contracts/wellsfargo.json](../../contracts/wellsfargo.json)。

## 一句话
- 版式主题：`wellsfargo`；列：date, description, amount, balance
- 卡尾 4 位：不进行内，卡号在表头
- 有 Type 列：否

## 类型枚举（Type / 行种类）
- 未从参考件探到，按契约 `open_questions` 处理，**不许猜**

## 描述符语法
| 行类 | 骨架 | 规则 |
| --- | --- | --- |
| card | `Card Purchase {mm/dd} {merchant} {place}` | no_card_tail, has_place |
| atm | `ATM Withdrawal {mm/dd} {place}` | — |
| check | `Check {number} {desc}` | — |
| zelle | `Zelle {Party Name}` | — |

## 必须出现 / 禁止出现
- 必须：（未探到，见 open_questions）
- 禁止：SAMPLE, billgen, PyMuPDF, Lorem

## 参考件里的真实行（照这个写）
```
10/08 | GROCERY MARKET SAMPLEVILLE 261008 DEBIT CARD PURCH | 86.23
```
```
10/12 | ELECTRIC UTILITY CO AUTOPAY 261012 ACH D0004567 | 104.15
```
```
10/15 | COFFEE SHOP NO 7 261015 DEBIT CARD PURCHASE 5814 | 12.50
```
```
10/17 | SAMPLE PHARMACY 261017 DEBIT CARD PURCHASE 5411 | 28.64
```

## 出处
- `src/billgen/synth/line_styles.py::wellsfargo_desc`
- `src/billgen/themes/wellsfargo/assets`
- `src/billgen/themes/wellsfargo/editor/preview.pdf`

## 抬头身份（必须原样）
- legal: `Wells Fargo Bank, N.A.`
- domain: `wellsfargo.com`
- phone: `1-800-869-3557`
- city_state_zip: `Des Moines, IA 50306-0335`

## 为什么这样写
- Wells Fargo 用动词前缀区分行类：`Card Purchase 12/11 …`、`ATM Withdrawal …`、`Check 1110 …`、`Zelle Payment To …`（大小写混排）；卡尾在表头不进行内。

## 本地依据（knowledge/）
- [knowledge/banks/wellsfargo.json](../../knowledge/banks/wellsfargo.json)
- [knowledge/regulations.md](../../knowledge/regulations.md)

## 字段字典
- [wellsfargo.md](../../docs/templates/wellsfargo.md)（每个顶层键的类型与样例）

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
