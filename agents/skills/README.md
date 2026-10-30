# 账户 skill 索引（一家银行一个账户 = 一份 skill）

每个可渲染模板（渲染器 id）对应两份文件，**同源**：

- 机器可读契约：`contracts/<template>.json` —— 校验/整形时读它（`src/billgen/contracts.py`）
- 人读 skill：`agents/skills/<template>.md` —— 你自己复核、或交给 agent 写描述符时读它

两份都由 `tools/contract_extract.py` 生成：参考件（`src/billgen/themes/<theme>/*.pdf`）自动提取 + `contracts/curated/<theme>.json`（人工核对过的规则，带出处）合并。
**没有参考件、也没人工规则的模板不会被生成规则**——它只会带着 `open_questions` 出现在这里，等你拿真实账单补。

| 账户（模板 id） | 银行 | 版式列 | 卡尾在行内 | Type 列 | skill | 契约 |
| --- | --- | --- | --- | --- | --- | --- |
| `boa-personal` | Bank of America 个人 | date, description, amount | 是 | 否 | [boa-personal.md](boa-personal.md) | [boa-personal.json](../../contracts/boa-personal.json) |
| `boa-business` | Bank of America 商业 | date, description, amount | 是 | 否 | [boa-business.md](boa-business.md) | [boa-business.json](../../contracts/boa-business.json) |
| `chase-personal` | Chase 个人 | date, description, amount, balance | 是 | 否（用行标签） | [chase-personal.md](chase-personal.md) | [chase-personal.json](../../contracts/chase-personal.json) |
| `chase-business` | Chase 商业 | date, description, amount, balance | 是 | 否（Deposit/Withdrawal） | [chase-business.md](chase-business.md) | [chase-business.json](../../contracts/chase-business.json) |
| `usbank-personal` | U.S. Bank 个人 | date, description, type, ref, amount | 否（在表头） | 是 | [usbank-personal.md](usbank-personal.md) | [usbank-personal.json](../../contracts/usbank-personal.json) |
| `usbank-business` | U.S. Bank 商业 | date, description, type, ref, amount | 否 | 是 | [usbank-business.md](usbank-business.md) | [usbank-business.json](../../contracts/usbank-business.json) |
| `wellsfargo` | Wells Fargo | date, description, amount, balance | 否 | 否（动词前缀） | [wellsfargo.md](wellsfargo.md) | [wellsfargo.json](../../contracts/wellsfargo.json) |
| `capitalone` | Capital One 360 | date, description, category, amount | 否 | 否（CATEGORY 列） | [capitalone.md](capitalone.md) | [capitalone.json](../../contracts/capitalone.json) |
| `sofi` | SoFi | date, description, type, amount, balance | 否 | 是（**倒序打印**） | [sofi.md](sofi.md) | [sofi.json](../../contracts/sofi.json) |
| `mercury` | Mercury（Column N.A.） | date, description, type, amount, balance | 否 | 是 | [mercury.md](mercury.md) | [mercury.json](../../contracts/mercury.json) |

## 怎么用

```bash
# 重新从参考件提取 + 合并人工规则（改了 curated/ 或换了参考件就跑）
PYTHONPATH=.:src .venv/bin/python tools/contract_extract.py
# 只看漂移，不写文件
PYTHONPATH=.:src .venv/bin/python tools/contract_extract.py --diff
# 单家
PYTHONPATH=.:src .venv/bin/python tools/contract_extract.py --templates mercury,sofi
```

交付前那条链路里，`contracts.conform()` 按契约整形、`contracts.check()` 校验；契约里 `open_questions` 非空的家，等于「这份规则还没被真件证过」，得先补证据再信它。
