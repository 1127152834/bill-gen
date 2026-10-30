# Agent 接入包（可搬到任意账单生成器）

这套东西不依赖本仓库的渲染器。你的应用只要能做三件事——**读契约、吐数据、渲染**——就能把 Agent 接进来。
包里的东西都是可复制的文本与 JSON，没有二进制、没有私有依赖。

```
kit/
├── README.md                     ← 你正在读：三步接入路线
├── docs/
│   ├── 01-integration-playbook.md  怎么接（分期、接口、错误处理、成本、回退）
│   ├── 02-statement-writing-norms.md  流水书写规范（跨行通用 + 逐家 11 套）
│   ├── 03-knowledge-base.md        知识库骨架 + 外部资料（带链接）
│   ├── 04-field-checklist.md       每种行类的必填字段清单
│   └── 05-evaluation.md            怎么验收「像不像真的」（含盲测方法）
├── schemas/                     persona / plan / ledger / findings / contract
├── prompts/                     可直接喂给模型的提示词（编排、导演、求解、选角、审计）
├── contracts/                   11 份真实契约（JSON，机器读）
├── skills/                      11 份人读说明书（Markdown，模型也能读）
└── tools/statement_lint.py      独立校验器（只用标准库，`python3 statement_lint.py --help`）
```

## 三步接入（别一次上全套）

### 第一步：**先让契约变成闸门（不接 AI）**
你现在的生成器已经在出账单了。先做这件事：把 `kit/contracts/*.json` 改造成你那边账户对应的契约（字段见 `kit/schemas/contract.schema.json`），
然后在你的出片前调一次 `statement_lint.py`：

```bash
python3 kit/tools/statement_lint.py --contract kit/contracts/chase-personal.json --data my-month.json
# 有问题就非 0 退出，把问题原文打出来
```

**验收**：你现在能出的每一份账单，lint 都是 0 问题。做不到就别急着接 AI —— AI 只会把错误放大。

### 第二步：**只接一个 Agent：生活导演（只出事件，不出金额）**
- 输入：档案（人设）→ 输出：`plan.json`（事件列表，无金额）。
- 用 `kit/prompts/02-director.md`，配上 `kit/schemas/plan.schema.json`。
- 你的求解器（若还没有，就先写个最土的：按信封把金额分下去）把 plan 变成行。
- **这一步不许让模型碰金额、不许让它拼描述符。**

**验收**：同一份档案跑 5 次，事件的类目构成、间隔、节日覆盖落在 `kit/docs/05-evaluation.md` 的区间内；闭合率 100%（期初+流水=期末）。

### 第三步：**接选角 + 审计团**
- 选角：`kit/prompts/04-cast.md`，只让它挑商户/门店号/地点/对方姓名，不碰金额。
- 审计：`kit/prompts/auditors/*.md` 六个角色，各自只输出 findings，**不改写**。
- 出片前：`conform`（按契约整形）→ `lint`（0 问题才出）。

**验收**：见 `kit/docs/05-evaluation.md` 的五项门槛。

## 一句话原则

> **AI 定意图，代码定数值，契约定格式。**
> 任何一步里，模型都不许同时决定「花多少钱」和「这笔钱长什么样」。

## 我这边能给你的现成材料

- `contracts/`：11 套（Chase 个人/企业、BoA 个人/企业、Wells Fargo、Capital One、SoFi、Mercury、U.S. Bank 个人/企业、Amex 口径）——**每套都带 evidence 与 open_questions**，BoA 那两份明确写着「没有参考 PDF，必须拿真实账单核对后才算契约」。
- `skills/`：每套一份人读规范（类型枚举、描述符语法、字段来源、常见错法）。
- `docs/02-statement-writing-norms.md`：这一份是你要的「流水书写规范」主体。
- `tools/statement_lint.py`：直接能用，零依赖。

## 本地知识库（knowledge/）

包里带的是**结论层**，不是原文全文：`sources.json`（每家银行/每部法规的取数方式与抓取指纹）、
`banks/<theme>.json`（法定名称、总部、费用事实、账单规则，逐条带 `source` + 原文 `quote` + `confidence`）、
`regulations.md`（Reg DD / Reg E / Reg CC / Nacha / 卡组织 / Check 21 条款 → 约束哪条规则，含我们当前违反的项）。

- 只有 `confidence: high` 的事实才能进契约；`low` 的只作提示。
- 每条事实的 `quote` 必须能在原文里找到，否则 `tools/build_bank_kb.py --check` 直接失败。
- 原文全文（`knowledge/raw/`）不随包分发，需要时在源仓库跑 `python tools/fetch_knowledge.py` 重新拉。
