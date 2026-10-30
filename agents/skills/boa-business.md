# Skill · 美国银行 · 商业账单（`boa-business`）

> 这是**这个账户**的填写规范与格式常识。写它的人只做归纳：内容全部来自参考件与引擎里已据真件实现的转换函数。
> 机器可读版：[../../contracts/boa-business.json](../../contracts/boa-business.json)。

## 一句话
- 版式主题：`boa`；列：date, description, amount
- 卡尾 4 位：印在行尾（`… Card 4499`）
- 有 Type 列：否

## 类型枚举（Type / 行种类）
- 未从参考件探到，按契约 `open_questions` 处理，**不许猜**

## 描述符语法
| 行类 | 骨架 | 规则 |
| --- | --- | --- |
| card | `{merchant} {mm/dd} Card {last4}` | has_card_tail, upper |
| ach | `DES:{des} ID:{ref} INDN:{holder} CO ID:XXXXX{co} {orig}` | upper |
| zelle | `Zelle payment to {party} Conf# {conf}` | — |
| check | `CHECK {number} {desc}` | upper |

## 必须出现 / 禁止出现
- 必须：（未探到，见 open_questions）
- 禁止：SAMPLE, billgen, PyMuPDF, Lorem

## 参考件里的真实行（照这个写）
- 未探到样例行（该模板可能只由解码器重放，不含可读文本层）

## 出处
- `docs/realism-patterns.md#4.4`
- `src/billgen/synth/adapters/boa.py::_descriptor`
- `src/billgen/synth/adapters/boa.py::_row`

## 待人工核对（不许凭记忆补）
- 卡尾 4 位应在行内，但参考件没探到（需人工核对样例行）
- 该主题没有参考 PDF：规则只能来自适配器实现，必须拿真实账单核对后才能当契约用

## 抬头身份（必须原样）
- legal: `Bank of America, N.A.`
- domain: `bankofamerica.com`
- phone: `1-800-432-1000`
- city_state_zip: `Tampa, FL 33622-5118`

## 为什么这样写
- BoA 流水按「存款 / 取款」两张表分栏，没有 Type 列；卡行末尾是 `… 10/13 Card 4499`；ACH 行是 NACHA 结构化描述符，不是商户名。

## 字段字典
- [boa-business.md](../../docs/templates/boa-business.md)（每个顶层键的类型与样例）

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
