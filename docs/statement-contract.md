# 账单常识归谁管：把「各家怎么写」变成可执行契约

> 一句话：**格式常识不能交给模型临场发挥，也不能散落在代码里靠注释维护。**
> 它应该是一份从真实件里提取、进 git、能被程序校验的「账单契约」，每家银行一份。
> 写它的人是「契约提取智能体 + 人类一次确认」，核对它的是渲染器断言、契约闸门和审计团，
> 运行时的 LLM 完全不需要知道格式。

---

## 一、「账单常识」其实是三件不同的事

| 层 | 内容 | 例子 | 谁负责 |
| --- | --- | --- | --- |
| **结构契约** | 有哪些字段、哪些区块、顺序、页面语义 | Mercury 两页：第 1 页账户摘要，第 2 页 `All Transactions`；U.S. Bank 披露页不编页码 | 契约提取 agent 写，渲染器执行 |
| **行描述符语法** | 每一行怎么写、放什么、放哪 | Chase `Card Purchase 10/13 Arcteryx Burlingame Burlingame CA Card 4499`；BoA `TST* WHOLEFDS MKT 105 12/11 Card 1347`；Mercury 卡行**只印商户**不印城市；Wells Fargo 行里**没有**卡号（卡号在表头） | 同上 |
| **版式** | 字体、坐标、磁码、水印、页脚 | Chase `DRE 703 219 28826 NNNNNNNNNNN`；U.S. Bank 只给账单页编号 | 现有 11 套模板（已 0 差异） |

现在这三层里，第三层是干净的；前两层散落在 `src/billgen/synth/adapters/*.py` 与
`src/billgen/synth/line_styles.py` 的函数分支里，靠注释和记忆维护——这就是为什么会出现
「Chase 存款行前十个空格」「Mercury 卡行塞城市」这类问题。

---

## 二、谁来写：契约提取智能体（一次性成本，之后只做漂移比对）

```
真实账单样本（我们已有 11 套参考 PDF + 若干真实件）
   ↓  ① 提取 agent（有工具：PDF span 提取、坐标、字体、文本层）
contracts/<template>.json   ← 结构 + 描述符语法 + 必备/禁止字面量 + 出处（仓库不带 PyYAML，用 JSON）
   ↓  ② 人类确认（只确认真实性，不手工誊写）
   ↓  ③ 生成/更新：JSON Schema、渲染器断言、闸门用例
```

**硬规矩：契约里每一条规则都必须带 `evidence`（样本文件 + 页 + span 或引用文本）。**
没有出处的规则不许写进契约——这条避免把「我以为的样子」当常识。

提取 agent 的产出示例（`contracts/mercury-personal.json` 已落地，字段同下）：

```yaml
template_id: mercury
kind: deposit
holder:
  source: profile          # 公司名取档案，不印 EIN（企业件才有）
columns: [date, description, type, amount, balance]
type_enum: [Card, ACH In, ACH Out]
descriptor:
  card: "{merchant}"                 # 卡行只印商户，城市与卡号都不进描述
  ach:  "{payee}"                    # 去掉 "... PAYMENT" 尾巴，方向由 Type 列表达
  atm:  "ATM WITHDRAWAL {city} {state}"
forbidden_literals: [SAMPLE, billgen, PyMuPDF, Bank of America]
required_literals: ["All Transactions", "End of Day Balance", "Banking services provided by Column N.A., Member FDIC"]
evidence:
  - src/billgen/themes/mercury/Demo-Bank-Statement-SAMPLE.pdf#p1
```

对比 `contracts/chase-personal.yaml`：卡号进行尾、`Card Purchase {mm/dd} {merchant} {city} {state} Card {last4}`、
磁码四段含 `DRE 703 219 28826`、行标签 `Deposit/Withdrawal` 不许留前导空格。
**同一套账本，两家银行的描述符语法完全不同**——这正是「常识」该被显式建模的地方。

---

## 三、谁来核对：三处，各自独立

1. **渲染器断言**（生成时）：渲染器只读契约，字段缺失或超出枚举直接报错，不出半成品。
2. **契约闸门**（CI，现有 `tools/realism_rules.py` 的升级）：对产出跑
   `src/billgen/contracts.py::check(template_id, data)` —— 类型枚举、描述符语法（正则）、
   禁止/必备字面量、金额两位小数、行数与摘要一致。**每家一份，不许共享默认值。**
3. **审计团的「契约审计」**（新架构第 ⑤ 层）：只读整张账单 + 契约，找「像拼接的地方」，
   例如同一页出现两种描述符风格、商户名带不该有的城市、Type 列混用两套写法。

---

## 四、怎么保证不腐坏：漂移比对

契约是**从样本提取的**，样本升级（换新版参考件）就要重新提取并 diff：

```
tools/contract_extract.py --template mercury-personal --sample <pdf> --diff
```

输出差异（新增列、标签改名、描述符变化），由人决定「这是新版本还是提取错」。
这样格式常识随时间保持真实，而不是靠谁记得。

---

## 五、和上一份设计的关系

在 [redesign-life-simulator.md](redesign-life-simulator.md) 里：

- **②导演 / ③求解器** 产出的是语义字段：`direction`、`merchant_class`、`amount`、`date`、
  `is_card`、`payee`。它们**不知道**任何银行格式。
- **④选角** 只负责挑商户与门店号，不负责拼字符串。
- **⑥渲染** 通过契约把语义字段映射成各行各自的写法。
- **审计团** 里新增「契约审计」，与会计/地理/统计/反真并列。

也就是说：**常识被固化在数据（契约）里，不活在模型的记忆里，也不活在代码的 if-else 里。**

---

## 六、落地顺序

1. 先把 11 套现有模板的隐式常识抽成 `contracts/*.yaml`（从已提交的参考 PDF 提取，
   现有 `line_styles.py` 的分支作为交叉验证），每家一份，带 `evidence`。
2. 写 `src/billgen/contracts.py::check()`，接进 `tools/realism_rules.py`，让产出一律过闸。
3. 把 `line_styles.py` 里的字符串拼接改成「读契约 + 填槽」，代码里不再出现银行名判断。
4. 之后再上求解器与导演——它们只需要面向语义字段，格式风险已经被契约隔离。


---

## 七、已经跑起来的部分（不是纸上设计）

- `contracts/mercury-personal.json`、`contracts/chase-personal.json`：两份真契约，每条带 `evidence`。
- `src/billgen/contracts.py`：`load / resolve / check / conform`。
  - `check()`：类型枚举、卡行不许带城市、卡行必须带卡号（Chase）、描述符长度、金额两位小数、禁止字面量。
  - `conform()`：**唯一执行点**——按契约把描述符改成这家银行的样子。
- `src/billgen/gen/runner.py`：收尾后调用 `conform()`，再过 `check()`，剩余问题进 `contract_warnings`。
- `tests/test_contracts.py`：5 条，包含「每家契约必须有出处」这条硬规矩。

跑第一遍就抓到真问题：Mercury 交付件里 23 行卡消费带了 `TEMPLE CITY CA`（Mercury 的卡行只印商户），
另有 2 行 ACH 带了 `PAYMENT` 尾巴——这两类问题以前要靠人眼看。
