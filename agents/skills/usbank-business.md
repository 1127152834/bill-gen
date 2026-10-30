# Skill · 美国合众银行 · 商业账单（`usbank-business`）

> 这是**这个账户**的填写规范与格式常识。写它的人只做归纳：内容全部来自参考件与引擎里已据真件实现的转换函数。
> 机器可读版：[../../contracts/usbank-business.json](../../contracts/usbank-business.json)。

## 一句话
- 版式主题：`usbank`；列：date, description, type, ref, amount
- 卡尾 4 位：不进行内，卡号在表头
- 有 Type 列：是

## 类型枚举（Type / 行种类）
- Purchase
- ATM
- ACH
- Zelle
- Check
- Fee
- Interest
- Transfer
- Payment
- Deposit
- Refund

## 描述符语法
| 行类 | 骨架 | 规则 |
| --- | --- | --- |
| card | `{merchant} {place}` | no_card_tail, upper |
| ach | `{payee}` | upper |

## 必须出现 / 禁止出现
- 必须：usbank.com, U.S. Bank National Association, Oshkosh, WI 54903-3528
- 禁止：SAMPLE, billgen, PyMuPDF, Lorem

## 参考件里的真实行（照这个写）
```
Aug 28 Zelle Instant | On 08/28/26 | PMT From Kiara Walker | PMT ID=COF4W8CDL9ZK | $ | 500.00
```
```
Aug 17 Zelle Instant | On 08/16/26 | PMT To Adrian Aragon | PMT ID=USB4fh5swvPL | $ | 10.00-
```

## 出处
- `src/billgen/synth/adapters/usbank.py::TYPE_LABEL`
- `src/billgen/synth/adapters/usbank.py::_row`
- `src/billgen/synth/adapters/usbank.py::_usb_account`
- `src/billgen/themes/usbank/assets/us_static_personal.pdf`
- `src/billgen/themes/usbank/assets/usbiz_static.pdf`

## 抬头身份（必须原样）
- legal: `U.S. Bank National Association`
- domain: `usbank.com`
- phone: `1-800-872-2657`
- city_state_zip: `Oshkosh, WI 54903-3528`

## 为什么这样写
- U.S. Bank 有 Type 与 Ref 两列；账号是 1-3-4-4 分组（`1 964 9173 9161`），不是掩码；卡尾不进行内。

## 字段字典
- [usbank-business.md](../../docs/templates/usbank-business.md)（每个顶层键的类型与样例）

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
