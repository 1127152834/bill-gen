# Skill · 水星银行（`mercury`）

> 这是**这个账户**的填写规范与格式常识。写它的人只做归纳：内容全部来自参考件与引擎里已据真件实现的转换函数。
> 机器可读版：[../../contracts/mercury.json](../../contracts/mercury.json)。

## 一句话
- 版式主题：`mercury`；列：date, description, type, amount, balance
- 卡尾 4 位：不进行内，卡号在表头
- 有 Type 列：是

## 类型枚举（Type / 行种类）
- Card
- ACH Out
- ACH In

## 描述符语法
| 行类 | 骨架 | 规则 |
| --- | --- | --- |
| card | `{merchant}` | no_city, no_card_tail, upper |
| ach | `{payee}` | upper, no_payment_tail |
| atm | `ATM WITHDRAWAL {city} {state}` | upper |

## 必须出现 / 禁止出现
- 必须：mercury.com
- 禁止：SAMPLE, billgen, PyMuPDF, Bank of America, Lorem

## 参考件里的真实行（照这个写）
```
Sep 9 | AWS EMEA | Card | -$84.32
```
```
Sep 17 | ACME CLIENT WIRE | Wire In | $3,600.00
```
```
Sep 25 | MONTHLY SERVICE FEE | Fee | -$15.00
```

## 出处
- `src/billgen/synth/line_styles.py#mercury_desc`
- `src/billgen/synth/line_styles.py::mercury_desc`
- `src/billgen/themes/mercury/Demo-Bank-Statement-SAMPLE.pdf`
- `src/billgen/themes/mercury/Demo-Bank-Statement-SAMPLE.pdf#p1`
- `src/billgen/themes/mercury/assets/letter/verification_letter_blank.pdf`
- `src/billgen/themes/mercury/assets/logo.pdf`

## 抬头身份（必须原样）
- legal: `Mercury Technologies, Inc.`
- domain: `mercury.com`
- phone: `1-415-767-0250`
- city_state_zip: `San Francisco, CA 94107`

## 为什么这样写
- Mercury 个人/企业同一个版式：卡消费只印商户名，不带城市与卡尾；ACH 只印对方名，不带 PAYMENT 尾巴。

## 本地依据（knowledge/）
- [knowledge/banks/mercury.json](../../knowledge/banks/mercury.json)
- [knowledge/regulations.md](../../knowledge/regulations.md)

## 字段字典
- [mercury.md](../../docs/templates/mercury.md)（每个顶层键的类型与样例）

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
