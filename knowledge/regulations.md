# 法规与规则对照表（Reg DD / Reg E / Reg CC / Nacha / 卡组织 / Check 21）

这一页不是法规摘要，是**给生成器用的对照表**：左边是条文（带来源 id 与原文片段，正文在 `knowledge/raw/`），右边是它约束我们哪条规则、当前实现满不满足。

规矩：**每条都要能回到原文**。`tools/build_bank_kb.py --check` 会校验 `knowledge/banks/*.json` 里的引用片段确实出现在原文里；这一页的引用用同样标准。

---

## 1. Reg DD（12 CFR 1030，Truth in Savings）— 定期对账单必须印什么

来源：`reg-dd-1030`（eCFR 官方 XML，2026-01-01 版）

| 条文 | 原文片段 | 约束我们什么 | 当前状态 |
| --- | --- | --- | --- |
| § 1030.6(a) | “the statement shall include the following disclosures: (1) Annual percentage yield earned … (2) Amount of interest … (3) Fees imposed” | 存款账户账单必须出现：earned APY、本期利息金额、本期被扣的费用 | 部分满足：费用行有（Fee 行），但**没有 earned APY / 利息金额**字段 → 契约待加 `required_literals: ["Annual Percentage Yield Earned", "Interest Paid"]`（个人支票账户） |
| § 1030.4 | “Account disclosures … clearly and conspicuously, in writing” | 开户披露页（我们模板里的 disclosure 页）必须与账单费用一致 | 未接校验：没有跨页一致性检查（账单印 $15 月费、披露页印别的数） |
| § 1030.11 | 透支服务的额外披露 | 若账单出现透支费，必须同时有透支服务披露 | 未覆盖：我们的模板没有透支场景 |

**落地动作**：给存款类契约加 `statement_disclosures` 段，列 § 1030.6 要求的三项；`statement_lint.py` 里加一条「缺项就报」。

---

## 2. Reg E（12 CFR 1005，电子资金划拨）

来源：`reg-e-1005`

| 条文 | 原文片段 | 约束我们什么 | 当前状态 |
| --- | --- | --- | --- |
| § 1005.9 | “periodic statements” 与 § 1005.7 初始披露的关系 | 有 ACH/卡交易的账户，账单必须能对照到初始披露里的错误处理条款 | 部分满足：模板有披露页，但没有「账单里必须印错误处理电话」的检查 |
| § 1005.11 | “Procedures for resolving errors … (i) An unauthorized electronic fund transfer … (iii) The omission of an electronic fund transfer from a periodic statement” | 错误处理时限 10 个工作日 / 45 天；账单上必须有可联系的电话 | 电话在契约里有（`phone_service`）；时限文案未校验 |
| § 1005.17 | 透支服务的同意（opt-in）要求 | 出现透支费必须有 opt-in 披露 | 未覆盖（同 Reg DD § 1030.11） |

**落地动作**：契约加 `dispute_phone`（必填，正则校验是 1-800 形式），lint 增加「存款账户必须有 dispute_phone」。

---

## 3. Reg CC（12 CFR 229，资金可用性）

来源：`reg-cc-229`、`fed-regcc-about`

| 条文 | 原文片段 | 约束我们什么 | 当前状态 |
| --- | --- | --- | --- |
| § 229.10 | “Next-day availability … cash deposits … not later than the business day after the banking day” | 账单上「Available Balance」与「Current Balance」的差额要能解释（在途存款） | 未实现：我们只有一个余额，没有 available/current 两个口径 |
| § 229.12 | “Local checks … available for withdrawal not later than the second business day” | 支票存款行不该当天全额可用 | 未实现（同上） |
| § 229.30–.36 | 电子支票、退票、背书、提示付款 | 支票行格式（Check Paid + 票号）与退票行的存在合理性 | 部分满足：票号有，退票场景没有 |

**落地动作**：若要做「Available Balance」，必须先有 § 229.10/.12 的可用日算法，否则两个余额会互相矛盾——这条**不做比做错好**。

---

## 4. Nacha / ACH 描述符（2026-03-20 生效）

来源：`ach-company-entry-description-2026`、`nacha-rules-2020`

原文（M&T 对规则变更的解读，2025 版）：

> “Company Entry Descriptions will require precise wording … effective March 20, 2026”
> “The Company Entry Description field can be found on the Batch Header record in positions 54-63”
> “PPD Credits for payment of wages … must include “PAYROLL” within the Company Entry Description field.”
> “Debits to collect payment for an e-commerce purchase must include “PURCHASE” …”

| 规则 | 约束我们什么 | 当前状态 |
| --- | --- | --- |
| 字段 10 字符（positions 54–63） | ACH 行描述符上限 10 字符（我们模板里行描述可以更长，但**语义字段**要能落进 10 字符） | 未建模：我们没有 ACH 语义字段，只有一坨描述 |
| 工资入账必须含 `PAYROLL` | 2026-03-20 之后的工资类 ACH 入账，描述里要有 `PAYROLL` | **当前违反**：生成器写的是 `CLIENT PAYMENT` / `LONG BOW AGENCY` 这类，既不是 PAYROLL 也不像公司名 |
| 电商扣款必须含 `PURCHASE` | 电商类 ACH 借记描述要含 `PURCHASE` | 未覆盖（我们没有电商 ACH 借记场景） |

**落地动作**（高优先）：给 ACH 行加 `sec_code`（PPD/CCD/WEB…）与 `company_entry_description` 两个字段，按类目生成（工资→`PAYROLL`、电商→`PURCHASE`、房租→`RENT`、水电→`UTILITY`），这比现在的自由文本真实得多。

---

## 5. 卡组织商户描述符

来源：`cybersource-descriptors`（各处理器字段长度与字符集）

| 事实 | 原文片段 | 约束我们什么 |
| --- | --- | --- |
| 描述符字段长度按处理器不同：13 / 22 / 25 / 53 | “Data Length: 13”“Elavon Americas Merchant Descriptors \| 22”“FDC Compass Merchant Descriptors \| 25”“Worldpay VAP Merchant Descriptors \| 53” | 行描述长度上限**必须逐家核对**，不能统一 30/36/42 拍脑袋 |
| 前缀必须与登记值一致 | “The prefix in the merchant descriptor field must be exactly the same as the prefix set in your Worldpay VAP account.” | 卡行前缀（`Card Purchase`、`POS PURCHASE`）是**登记在收单侧的**，不是随机加的 |
| 允许字符集 | “Numbers / Letters / … ampersand (&), asterisk (*), dash (-), pound sign (#), comma, and period” | 描述符里不该出现 `_`、`$`、`|` 之类；我们的 lint 目前只查大小写 |
| 组合格式 | “<12-character prefix>*<9-character product description>” | `AMAZON.COM*2H4KL9` 这种 `*` 结构有出处 |

**落地动作**：契约 `descriptor.max_chars` 改成带 `source` 的字段（哪家处理器、多少字符），lint 加「字符集白名单」。

---

## 6. 支票与 Check 21

来源：`check21-fed`、`reg-cc-229`

| 事实 | 约束我们什么 | 当前状态 |
| --- | --- | --- |
| 替代支票（substitute check）可合法替代纸质支票 | 账单上 `Check Paid` 行不需要印支票影像，但要有票号 | 满足（Chase/BoA/Amex 票号规则） |
| 退票与notice of nonpayment 由付款行/收款行分别负责（§ 229.31–.33） | 退票行（Returned Item）是合法行类型，金额回到账户 | 未实现（没有退票场景） |

---

## 汇总：这份对照表暴露的三个真问题

1. **存款账单缺 Reg DD § 1030.6 的三项披露**（earned APY、利息金额、费用汇总）——真实账单几乎都有，我们一份都没有。
2. **ACH 描述符不符合 2026-03-20 生效的 Nacha 规则**（工资要含 `PAYROLL`）——我们现在写 `CLIENT PAYMENT`，这在真实账单上是「不知名小额支付」，不是工资。
3. **没有 available / current 两个余额口径**（Reg CC）——真实账单两者经常差几百美元，我们只有一个数。

三条都不需要 AI，属于契约与求解器的事。要做到「符合自然规律」，这三条比再调十个商户名更值钱。
