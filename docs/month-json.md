# 每月一份模板 JSON（交付物）与三层校验

账单生成流程的终点从「PDF」改成「**每月一份模板 JSON**」：用户自己把 JSON 导进模板出账单，
程序只保证交付件结构正确、内容自洽、跨月接得上。

- 模块：`src/billgen/synth/monthpack.py`（拆月 + 校验）、`src/billgen/delivery.py`（打 zip 交付包）
- 入口：HTTP `POST /api/v1/synth/months`、MCP 工具 `month_json`（不落盘）/ `month_files`（落盘 + 报告 + 清单）
- 测试：`tests/test_monthpack.py`（11 条）、`tests/test_delivery.py`（3 条）

## 两条落地路径

| 谁在用 | 怎么走 | 结果 |
| --- | --- | --- |
| LangGraph 工作流（`flow/`） | 渲染节点把 `billgen.deliver` 设成 `'json'`（默认），逐月调 MCP `month_files` | 结果里 `renders` 为空、`monthFiles[]` 有值；每份带 `path/rows/bytes/sha256/ok` |
| 直接调 Python 服务 | `POST /api/v1/synth/months` 或 MCP `month_files` | 落盘 JSON + 校验报告 + 清单（或打 zip） |

模型侧的规范写在技能里：`flow/skills/bank-forms/SKILL.md` 的「交付物：每月一份模板 JSON」一节，
字段含义在 `flow/skills/bank-forms/fields/<template>.md`（由 `tools/gen_bank_forms_assets.py` 生成，`--check` 验同源）。

页面上：结果页的「模板 JSON」面板按份列出月份/行数/大小/校验状态，每份给下载链接，
底部给 `validation-report.json` 与 `manifest.json`。下载走 `GET /api/v1/renders/<文件名>`
（只认一层文件名 + `pdf/json/png` 后缀）。`deliver: 'pdf'` 时仍会出片，旧面板照旧显示。

## 为什么在账本层拆月

十套模板的日期/期初字段各不相同（`period.start`、`period_start`、`summary.period_start`、
`period_range`、`statement_period`；`begin_balance`、`beginning_balance`、`opening_balance`、
`current_balance`），如果为「拆月」再写十套格式映射，等于把各家的字段规则实现两遍，
迟早两边跑偏。

做法是：把账本按自然月分桶、重算每月期初期末，再让**原适配器**照常出数据。
每份 JSON 都走同一条已经验证过的 `build_data()` 链路，新增的只有分桶与校验。

## 交付包结构

`POST /api/v1/synth/months` 返回 `application/zip`：

| 文件 | 内容 |
| --- | --- |
| `<模板>-<YYYY-MM>.json` | 该月的模板数据，直接可导入 |
| `validation-report.json` | `batch`（跨月结论）+ `months[]`（每月结构/内容/提醒全文） |
| `manifest.json` | 文件清单：月份、sha256、字节数、行数、期初期末、是否通过 |

响应头：`X-Month-Count`（几份）、`X-Month-OK`（`1`/`0`，整批是否全过）。
失败月份**不会**被藏起来：`manifest.ok=false`、报告里那份 `ok=false`，其他月份照常交付。

```bash
curl -sS -X POST http://127.0.0.1:8800/api/v1/synth/months \
  -H 'Content-Type: application/json' \
  -d '{"template":"capitalone","result":{...GenerationResult...}}' \
  -o months.zip
unzip -l months.zip
```

MCP 侧同名能力：`month_json(template_id, result, account?, cycle?, bank_name?, months?)`
直接返回 `files[]`（每份 `data` + `report`）与 `batch`，不落盘，方便 LLM 边生成边自检。

## 三层校验

### 1. 结构（`structure`）

| 规则 | 含义 |
| --- | --- |
| `missing_key` | 模板默认数据里非空的键，交付件没有 |
| `missing_optional` | 默认值为空的键没给（只提醒） |
| `type_mismatch` | 顶层值类型不对（金额允许 `"1,234.56"` 这种字符串） |
| `row_type_mismatch` | 流水行内字段类型不对（金额写坏会被这条抓住） |
| `date_unparseable` / `money_unparseable` | 日期/金额解析不出来 |
| `row_field_absent` / `unknown_row_field` / `row_field_inconsistent` | 行字段与模板默认行不完全一致（提醒）；同一数组内字段不齐（提醒） |

判断「这是金额还是日期」不用字段名猜，而是**拿模板自己的默认值当标尺**：
默认值长成 `1,500.00` 就必须是金额，长成 `Jan 4` 就必须是日期。
`micr.cycle="1"`、`account_display="3 020 6041 1196"` 这类编号不会被误判。

### 2. 单月内容（`content`）

| 规则 | 含义 |
| --- | --- |
| `period_out_of_month` | 账期跑到别的月份去了 |
| `balance_not_closed` | 期初 + 收支 ≠ 期末 |
| `empty_month` | 这个月一行流水都没有 |
| `placeholder_text` | 出现 SAMPLE/EXAMPLE 之类占位内容 |
| `json_total_mismatch` | **交付件自己**的流水合计 ≠ 期末 − 期初 |
| `json_row_count_mismatch` | 交付行数 ≠ 账本行数 |
| `no_transactions_found` | 找不到任何流水行（导入后是空账单） |
| `build_failed` | 适配器/自检抛错（例如账户类型与模板不匹配） |
| `rows_dropped` 等 | 复用 `lint.issues()` 的既有规则 |

`json_total_mismatch` / `json_row_count_mismatch` 是**不看账本**的自相加：有人手改 JSON
里的一个金额，只有这两条会立刻不平。

### 3. 跨月连续（`batch`）

| 规则 | 含义 |
| --- | --- |
| `balance_gap` | 上个月期末 ≠ 下个月期初 |
| `month_gap` | 月份不连续（中间缺月） |
| `template_mix` | 同一批里混了不同模板 |
| `no_months` | 一份都没出 |
| `failed` | 有月份未通过（列月份名） |

## 行为约定

- **选 3 个月就 3 份**：`months` 不传按账本实际月份出；传了只出这几月，但期初仍按
  前面月份的余额累计（不会把跳过的月份收支凭空抹掉）。
- **空月份不出件**：那一个月没有流水就不该有账单，硬造一份等于编数据。
- **失败不连坐**：某个月 `build_failed`，其他月份照常交付，整批 `ok=false` 并列出失败月份。
- **一律带 `ensure_ascii=False`**：中文原样落盘，方便人核对与 diff。

## 已知边界

1. 行字段与模板默认行不完全一致只算提醒（例如 mercury 默认行有 `txid`，适配器给的是 `id`，
   模板用 `.get()` 取，导得进去）。
2. 校验不判断「这笔消费合不合理」（金额与商户是否匹配），那是生活模型的事。
3. 交付包不含 PDF；需要 PDF 仍可走 `POST /api/v1/synth/render` 或 MCP `render`。

## 回滚

只动这三个文件 + 两个测试：

```bash
git revert <本轮提交>
# 或
git checkout <上一提交> -- src/billgen/synth/monthpack.py src/billgen/delivery.py \
  src/billgen/mcp/server.py src/billgen/server/http.py \
  tests/test_monthpack.py tests/test_delivery.py
```
