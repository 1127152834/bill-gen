# 账单生活模拟器 · Agent 组合

一套「让账单像真过出来的」多智能体组合。核心分工一句话：

> **AI 决定「这个人怎么过这个月」，代码决定「每一笔多少钱」，审计只否决不改写。**

- 架构说明：[../docs/redesign-life-simulator.md](../docs/redesign-life-simulator.md)
- 格式常识：[../docs/statement-contract.md](../docs/statement-contract.md)
- 契约文件：[../contracts/](../contracts/)

## 账户 skill（每家银行一份）

写描述符、复核行格式前先读 `agents/skills/<template>.md`（索引：`agents/skills/README.md`）。
这些 skill 与 `contracts/<template>.json` 同源，都由 `tools/contract_extract.py` 生成，人工规则在 `contracts/curated/`。

## 交接链

```
profile.json ──①persona-compiler──▶ persona.json
persona.json + 上月 ledger ──②life-director──▶ plan/<YYYY-MM>.json（只出事件，不出金额）
plan + persona ──③solver（代码）──▶ ledger/<YYYY-MM>.jsonl（唯一真相，闭合）
ledger + POI/品牌表 ──④cast-merchants──▶ ledger + descriptors
ledger + contract + persona ──⑤auditors（6 个）──▶ findings.json
findings 为空 ──⑥renderer──▶ month JSON / PDF（11 套模板）
findings 非空 ──回到②（带证据重排，最多 2 轮）
```

## 角色表

| # | Agent | 提示词 | 输入 → 输出 | 能不能改数字 |
| --- | --- | --- | --- | --- |
| ① | 档案编译器 | [prompts/01-persona-compiler.md](prompts/01-persona-compiler.md) | 自由文本/表单 → `persona.json` | 只给区间，不定单笔 |
| ② | 生活导演 | [prompts/02-life-director.md](prompts/02-life-director.md) | persona + 上月末态 + 日历 → `plan.json` | ❌ 只给区间与理由 |
| ③ | 求解器 | [prompts/03-solver-spec.md](prompts/03-solver-spec.md) | plan → `ledger.jsonl` | ✅ 数值唯一的产地 |
| ④ | 选角 | [prompts/04-cast-merchants.md](prompts/04-cast-merchants.md) | 事件 + 城市 → 商户/门店号/描述符 | ❌ 不改金额 |
| ⑤ | 审计团 ×6 | [prompts/auditors/](prompts/auditors/) | ledger + contract + persona → findings | ❌ 只否决 |
| ⑥ | 契约提取 | [prompts/05-contract-extractor.md](prompts/05-contract-extractor.md) | 真实样本 PDF → `contracts/<t>.json` | ❌ |
| — | 总指挥 | [prompts/00-conductor.md](prompts/00-conductor.md) | 驱动流程、收 findings、决定重排 | ❌ |

## 机器可读清单

[manifest.json](manifest.json) 列出每个 agent 的提示词文件、输入/输出 schema、工具、建议模型档与温度。
`tools/check_agents.py` 会校验「清单里提到的文件都存在、schema 都能解析、每个提示词都有必需小节」，
`tests/test_agents.py` 把它接进了 CI。

## 三条不许破的规矩

1. **数字只在求解器产生。** 任何 LLM 输出里出现具体金额（除区间与税表常数值）都算违规。
2. **审计只否决，不改写。** findings 必须带 `rows[]` 或「缺失证据」，回给导演重排。
3. **格式由契约决定。** 描述符、类型枚举、行尾带不带城市卡号，一律查 `contracts/<template>.json`。

## 验收标准

三个门槛，缺一不可：
- **闭合率 100%**（会计恒等式、跨月接续一票否决）
- **分布回归**：生成 500 个月，与真实参数 KS/卡方 p>0.01
- **辨识率 ≤65%**：20 真 + 20 假混排盲测（随机=50%）

细节：[rubric.md](rubric.md)｜跑法：[playbook.md](playbook.md)
