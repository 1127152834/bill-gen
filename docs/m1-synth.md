# M1 · 场景生成结果 → 像素级账单（`billgen.synth`）

M0 把整套场景生成器（原 `bill-gen-game`，`bill-hunter`）搬进 `flow/`，M1 在 Python 侧
补上一层「账本 → 模板数据」的翻译，让「有多可信的数据」和「有多像真的版式」接在一起。

```
flow/（Node，LLM 流水线）                     billgen（Python，版式引擎）
GenerationResult JSON ──→ from_game_result ──→ LedgerDocument ──→ 适配器 ──→ 模板 JSON ──→ PDF
```

进程边界仍然是既有的渲染器，`synth` 只做翻译与自检，不重写任何版式。

## 1. 数据结构

`LedgerDocument`（`src/billgen/synth/ledger.py`）是中间规范：**一个账户、一个账期、一条自洽的余额链**。

| 字段 | 说明 |
| --- | --- |
| `bank` | `BankIdentity{name, legal, domain, phone}`，由银行名 + 种子派生 |
| `holder` | `Holder{name, address_lines, is_business}`，含 `zip5` / `city_state` 便捷属性 |
| `account` | `AccountRef{kind, last4, number, routing, product, is_credit, display}` |
| `period` | `Period{start, end, label_long, label_through, days}` |
| `rows` | `LedgerRow` 序列：日期、入账日、描述、备注、类型、方向、金额（恒正）、余额、商户、支票号 |
| `opening_cents` / `closing_cents` | 期初 / 期末，`closing = opening + Σ贷 − Σ借` |
| `credit` | `CreditTerms{limit, apr, statement, minimum_due, due_date, interest}` |

上游给的是「多账户 × 多账期」；`from_game_result(payload, account=..., cycle=...)`
按 `accountType` 选账户、按 `months[cycle]` 选账期、按日期窗口过滤流水，然后**重建余额链**——
不信任上游的 `runningBalanceCents`（跨期时它只对第一期成立）。

派生字段（同种子可复现）：

- `aba_routing(seed)`：ABA 3-7-1 校验位正确的 9 位路由号；
- `account_number(seed, last4)`：12 位账号，末四位沿用上游 `accountLast4`；
- 支票号：`CHECK` 类型按账期顺序连续编号。

## 2. 适配器

`src/billgen/synth/adapters/` 是唯一知道「某个模板要什么字段」的地方：

| 适配器 | 模板 | 说明 |
| --- | --- | --- |
| `boa.py` | boa-personal / boa-business | 分区表（deposits/withdrawals/fees）+ summary/totals/daily/stats |
| `wellsfargo.py` | wellsfargo | 单表流水（deposit/withdrawal/balance 三列）+ 银行联系方式 |
| `chase.py` | chase-personal / chase-business | personal 用统一 `transactions`；business 必须分区且支票行带 `no/paid/caret` |
| `usbank.py` | usbank-personal / usbank-business | 分区 + `imb_serial` + `daily` 日终表 |
| `simple.py` | amex / mercury / capitalone / sofi | 金额为浮点、字段扁平的单表模板 |

`BINDINGS` 声明每个模板接受的账户类型。13 套版式全部是**存款账户对账单**，
所以信用账户（`credit`）没有绑定：`account_mismatch` 会直接报错，而不是把信用卡流水硬塞进
对账单版式。

金额格式按模板各处的既定写法输出（`plain` / `grouped` / `currency` / `float`），
描述符按列宽截断（`clip`），排版溢出的风险由「不丢行」自检兜底。

## 3. 渲染前自检（`lint.py`）

`verify(ledger, data, template_id)` 在渲染前跑一遍账本与映射的双向校验，任何一条不通过就抛
`SynthLintError`（带 `issues` 列表），不产出半对的 PDF：

| code | 含义 |
| --- | --- |
| `balance_not_closed` | 期初 + Σ贷 − Σ借 ≠ 期末 |
| `amount_not_positive` | 流水金额出现非正数（方向应交给 `direction`） |
| `row_out_of_period` | 流水日期落到账期外 |
| `negative_balance` | 某笔入账后余额为负 |
| `rows_unsorted` | 流水未按日期升序 |
| `rows_dropped` | 适配器承载的行数 ≠ 账本行数（截断/丢行） |
| `account_mismatch` | 账本账户类型不在模板接受范围内 |
| `no_binding` | 模板没有账本映射 |

## 4. 用法

```bash
# CLI：生成结果 → 指定模板 PDF
billgen synth render chase-personal --from result.json -o out.pdf
billgen synth render wellsfargo --from result.json --account checking --cycle 0 -o wf.pdf

# Python
from billgen.synth import from_game_result, render, fingerprint
ledger = from_game_result(payload, account="checking", cycle=-1)
result = render("boa-personal", ledger)          # result.pdf / result.pages
```

CLI 在 stdout 打一行 JSON（`template/account/period/rows/pages/bytes/elapsed_ms/output`），
和其它子命令一致，便于脚本消费。

同一份账本重复渲染得到**完全相同的字节**：`render()` 默认把 trailer 里的随机 `/ID`
归一化（`stable_bytes=True`），`fingerprint(pdf)` 则给出「页尺寸 + 文本层 + 图片」的版式指纹。

## 5. 水印与来源标记

`adapters/_common.py` 统一给出样例标记，任何适配器都不会漏：

- PDF 元数据：`title` 取模板族的真实出件标题（`_common.metadata()`，如 `Chase Online Statement`），
  `producer/creator` 用该行真实单据供应商（`OpenText Output Transformation Engine`、`Ricoh AFP2PDF` 等）；
  样例身份只由可见水印表达 —— 元数据里再写 SAMPLE 会与供应商指纹自相矛盾，等同一行 `pdfinfo` 就能验伪；
- 页面水印：默认 `watermark=True`，即每页可见的 `SAMPLE`；
- 银行身份：由场景生成器给出的虚构行名（上游提示词本身就禁止使用真实银行名）。

时间戳由账期结束日派生（`D:YYYYMMDD000000Z`），既稳定又符合「对账单在账期结束后生成」的直觉。

## 6. HTTP 与工作台

服务端把同一层能力暴露成两个接口（`src/billgen/server/http.py`、`server/application.py`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/synth/capabilities` | `{accounts, templates[{id, display_name, accounts}]}` |
| POST | `/api/v1/synth/render` | `{template, result, account?, cycle?, bank_name?, watermark?, options?}` → 201 任务 |

- 出错按既有约定返回 `{"error":{"code","message"},"request_id"}`：`invalid_request` 400、
  `template_not_found` 404、`static_template` 422、`template_unsupported` 422、
  `account_mismatch` 422、`invalid_result` 422、`ledger_check_failed` 422、`result_too_large` 413；
- 响应在标准任务字段外多给 `ledger`（账户、银行、账期、行数、期初期末）与 `data`（映射后的模板数据），
  前端因此可以把「生成 → 微调 → 导出」串成一条链；
- PDF 在服务端同样做 trailer `/ID` 归一化（未加密时），同一请求重复调用字节一致。

工作台（`web/index.html` 的「场景导入」）不自己解析结果包：粘贴或选择文件后 POST 上述接口，
成功后把 `data` 灌进表单与 JSON 编辑器，清掉草稿脏标记，并把页数、行数、账期写进预览标题栏。

## 7. 已知边界

- `alliant` / `ally` 是纯前端模板，没有服务端渲染，`is_supported()` 返回 `False`；
- 品牌美术由 M2 的「品牌替换包」处理（见 `docs/m2-branding.md`）：行标物理替换、amex/chase 字段数据驱动，
  usbank/sofi 烙死的字符串登记在 `billgen.audit.KNOWN_GAPS` 并逐条列明；
- `flow` 单次真实生成需要 LLM 与搜索 API；离线开发用 `LLM_FAKE=1`，其结果形状与本层契约一致。

## 8. 验证基线

```bash
.venv/bin/python -m pytest -p no:warnings -q        # 417 passed / 16 skipped
.venv/bin/python tools/render_regression.py check   # 32 个版式指纹
.venv/bin/python -m ruff check src tests tools
.venv/bin/python -m mypy src/billgen/synth
```

`tests/test_synth.py` 覆盖：金额/日期格式化、账本闭合与支票号、11 个适配器的「不丢行」、
账户类型守卫、自检失败路径、11 个模板的端到端渲染、渲染确定性、CLI。
`tests/test_synth_api.py` 覆盖 HTTP 契约：能力表、四种模板直出（含不丢页）、商业账户、
账户不匹配、静态模板、未知模板、缺字段、闭合失败、未知字段、字节稳定。
