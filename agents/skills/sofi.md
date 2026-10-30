# Skill · 索菲银行（`sofi`）

> 这是**这个账户**的填写规范与格式常识。写它的人只做归纳：内容全部来自参考件与引擎里已据真件实现的转换函数。
> 机器可读版：[../../contracts/sofi.json](../../contracts/sofi.json)。

## 一句话
- 版式主题：`sofi`；列：date, description, type, amount, balance
- 卡尾 4 位：不进行内，卡号在表头
- 有 Type 列：是（按打印顺序倒序，最新在上）

## 类型枚举（Type / 行种类）
- Card Purchase
- Withdrawal
- Check
- Fee
- Interest Earned
- Transfer
- Direct Payment
- Deposit

## 描述符语法
| 行类 | 骨架 | 规则 |
| --- | --- | --- |
| card | `{merchant} {place}` | has_place, no_card_tail |
| transfer | `To Savings - {last4}` | — |

## 必须出现 / 禁止出现
- 必须：sofi.com, 1-855-456-7634, SoFi Bank, N.A., Cottonwood Heights, Utah 84121
- 禁止：SAMPLE, billgen, PyMuPDF, Lorem

## 参考件里的真实行（照这个写）
```
Sep 30, 2026 | Interest Earned | Interest earned | Transaction ID: 138-1 | $2.15
```
```
Sep 25, 2026 | Withdrawal | To Savings - 1943 | Transaction ID: 137-38317001 | -$2,000.00
```
```
Sep 17, 2026 | Deposit | STRIPE TRANSFER | Transaction ID: 133-12773001 | $4,216.26
```
```
Sep 3, 2026 | Direct Payment | CHASE CREDIT CRD EPAY | Transaction ID: 132-445765001 | -$568.41
```

## 出处
- `src/billgen/synth/line_styles.py::_SOFI_TYPES`
- `src/billgen/synth/line_styles.py::sofi_desc`
- `src/billgen/themes/sofi/SoFi-Statement-SAMPLE.pdf`
- `src/billgen/themes/sofi/assets/logo.pdf`

## 抬头身份（必须原样）
- legal: `SoFi Bank, N.A.`
- domain: `sofi.com`
- phone: `1-855-456-7634`
- city_state_zip: `Cottonwood Heights, Utah 84121`

## 为什么这样写
- SoFi 有 Type 列（Card Purchase / Direct Payment / Transfer…），并且**按时间倒序**打印；内部转账写成 `To Savings - 1943`。

## 字段字典
- [sofi.md](../../docs/templates/sofi.md)（每个顶层键的类型与样例）

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
